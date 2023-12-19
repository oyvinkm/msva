import numpy as np
import torch
from torch import nn
from torch.nn import LayerNorm, Linear, ReLU, Sequential, LeakyReLU
from torch_geometric.data import Batch, Data
from torch_geometric.nn.conv import GraphConv, MessagePassing
from torch_geometric.nn.pool import ASAPooling, SAGPooling, TopKPooling
from torch_geometric.utils import degree, coalesce
from torch_scatter import scatter
from loguru import logger

def pool_edge(m_id, edge_index, edge_attr: torch.Tensor, aggr: str="mean"):
    r"""Pools the edges of a graph to a new set of edges using the idxHR_to_idxLR mapping.

    Args:
        idxHR_to_idxLR (torch.Tensor): A mapping from the old node (or higher resolution) indices to the new (or lower resolution) node indices.
        edge_index (torch.Tensor): The old edge indices.
        edge_attr (torch.Tensor): The old edge attributes.
        aggr (str, optional): The aggregation method. Can be "mean" or "sum". Defaults to "mean".

    Returns:
        Tuple[torch.Tensor, torch.Tensor]: The new edge indices and attributes.
    """
    num_nodes = len(m_id)# number of nodes in the lower resolution graph
    if not torch.is_tensor(edge_index):
        edge_index = torch.tensor(edge_index)
    if edge_index.numel() > 0:
        edge_index, edge_attr = coalesce(edge_index, edge_attr, num_nodes, reduce=aggr) # aggregate edges
    return edge_index, edge_attr

class MessagePassingEdgeConv(MessagePassing):
    def __init__(self, channel_in, channel_out, args):
        super(MessagePassingEdgeConv, self).__init__()
        self.messagePassing = MessagePassingBlock(hidden_dim=channel_in, latent_dim = channel_out, num_blocks=args.num_blocks, args = args)
        self.edge_conv = WeightedEdgeConv()
        self.args = args
    
    def forward(self, b_data):
        x, g, w = b_data.x, b_data.edge_index, b_data.weights
        x = self.messagePassing(x, g)
        ew, w = self.edge_conv.cal_ew(w, g)
        # Does edge convolution on nodes with edge weigths
        x = self.edge_conv(x, g, ew)
        # Does edge convolution on position with edge weights
        if len(w.shape) < 2:
            w = w.unsqueeze(dim = 1)
        b_data.weights = w
        b_data.x = x
        return b_data
    

class GCNConv(MessagePassing):
    """
    Classic MessagePassing/Convolution
    """

    def __init__(self, in_channels, out_channels):
        super(GCNConv, self).__init__(aggr="add")  # "Add" aggregation.
        self.lin = torch.nn.Linear(in_channels, out_channels)

    def forward(self, x, edge_index):
        # x has shape [num_nodes, in_channels]
        # edge_index has shape [2, E]

        # Step 1: Add self-loops to the adjacency matrix.
        # Step 2: Linearly transform node feature matrix.
        x = self.lin(x)

        # Step 3-5: Start propagating messages.
        return self.propagate(edge_index, size=(x.size(0), x.size(0)), x=x)

    def message(self, x_j, edge_index, size):
        # x_j has shape [num_edges, out_channels]

        # Step 3: Normalize node features.
        row, col = edge_index
        deg = degree(row, size[0], dtype=x_j.dtype)
        deg_inv_sqrt = deg.pow(-0.5)
        norm = deg_inv_sqrt[row] * deg_inv_sqrt[col]

        return norm.view(-1, 1) * x_j

    def update(self, aggr_out):
        # aggr_out has shape [num_nodes, out_channels]

        # Step 5: Return new node embeddings.
        return aggr_out

class WeightedEdgeConv(MessagePassing):
    """
    Weighted Edge Convolution used for pooling and unpooling.
    """

    def __init__(self):
        super().__init__(aggr="add", flow="target_to_source")

    def forward(self, x, g, ew, aggragating=True):
        # aggregating: False means returning
        i = g[0]
        j = g[1]
        if len(x.shape) == 3:
            weighted_info = x[:, i] if aggragating else x[:, j]
        elif len(x.shape) == 2:
            weighted_info = x[i] if aggragating else x[j]
        else:
            raise NotImplementedError("Only implemented for dim 2 and 3")
        weighted_info *= ew.unsqueeze(-1)
        target_index = j if aggragating else i
        aggr_out = scatter(
            weighted_info, target_index, dim=-2, dim_size=x.shape[-2], reduce="sum"
        )
        return aggr_out

    @torch.no_grad()
    def cal_ew(self, w, g):
        if w is None:
            w = torch.ones_like()
        deg = degree(g[0], dtype=torch.float, num_nodes=w.shape[0])
        normed_w = w.squeeze(-1) / deg
        i = g[0]
        j = g[1]
        w_to_send = normed_w[i]
        eps = 1e-12
        aggr_w = (
            scatter(w_to_send, j, dim=-1, dim_size=normed_w.size(0), reduce="sum") + eps
        )
        ec = w_to_send / aggr_w[j]
        return ec, aggr_w

class MessagePassingBlock(torch.nn.Module):
    """
    Just combines n number of message passing layers
    """

    def __init__(self, hidden_dim, latent_dim, args, num_blocks=None):
        super(MessagePassingBlock, self).__init__()
        if num_blocks is None:
            self.num_blocks = args.num_blocks
        else:
            self.num_blocks = num_blocks
        self.hidden_dim = hidden_dim
        self.latent_dim = latent_dim
        self.processor = nn.ModuleList()
        assert self.num_blocks >= 1, "Number of message passing layers is not >=1"
        
        processor_layer = self.build_processor_model()
        for i in range(self.num_blocks):
            if i == 0:
                self.processor.append(processor_layer(self.hidden_dim, self.latent_dim))
            else:
                self.processor.append(processor_layer(self.latent_dim, self.latent_dim))

    def build_processor_model(self):
        return GCNConv

    def forward(self, x, edge_index):
        # Step 1: encode node/edge features into latent node/edge embeddings
        # step 2: perform message passing with latent node/edge embeddings
        for i in range(self.num_blocks):
            x = self.processor[i](x, edge_index)
        return x

class MessagePassingLayer(torch.nn.Module):
    """
    Kinda like a U-Net but with Message Passing Blocks.
    The Multiscale Autoencoder consists of multiple of these
    """

    def __init__(self, hidden_dim, latent_dim, args, bottom = False, first_up = False):
        super(MessagePassingLayer, self).__init__()
        self.hidden_dim = args.hidden_dim
        self.l_n = args.mpl_layers
        self.args = args
        self.bottom = bottom
        self.first_up = first_up
        self.hidden_dim = hidden_dim
        self.latent_dim = latent_dim
        """ if args.latent_dim is not None and (bottom or  first_up):
            if first_up:
                self.hidden_dim = args.latent_dim
                self.latent_dim = args.hidden_dim
            else:
                self.latent_dim = args.latent_dim
        else:
            self.latent_dim = args.hidden_dim """
        self.num_blocks = args.num_blocks
        self.down_gmps = nn.ModuleList()
        self.up_gmps = nn.ModuleList()
        self.unpools = nn.ModuleList()
        self.bottom_gmp = MessagePassingBlock(hidden_dim=self.latent_dim, latent_dim=self.latent_dim, args=args)
        self.edge_conv = WeightedEdgeConv()
        self.pool = self._pooling_strategy()
        self.pools = nn.ModuleList()
        if self.args.mpl_ratio is None:
            self.mpl_ratio = 0.5
        else:
            self.mpl_ratio = self.args.mpl_ratio

        for i in range(self.l_n):
            if i == 0:
                self.down_gmps.append(
                        MessagePassingBlock(hidden_dim=self.hidden_dim, latent_dim = self.latent_dim, args=args)
                    )
            else:
                self.down_gmps.append(
                       MessagePassingBlock(hidden_dim=self.latent_dim, latent_dim = self.latent_dim, args=args)
                    )
            self.up_gmps.append(
                MessagePassingBlock(hidden_dim=self.latent_dim, latent_dim=self.latent_dim, args=args)
            )
            self.unpools.append(Unpool())
            if self.args.pool_strat == "ASA":
                self.pools.append(
                    self.pool(
                        in_channels=self.latent_dim, ratio=self.mpl_ratio, GNN=GraphConv
                    )
                )
            else:
                self.pools.append(
                    self.pool(self.latent_dim, self.mpl_ratio)
                )

    def forward(self, b_data):
        """Forward pass through Message Passing Layer"""
        down_outs = []
        cts = []
        down_masks = []
        down_gs = []
        batches = []
        ws = []
        b_data.edge_weight = None
        edge_attr = b_data.edge_attr
        b_data.weights = b_data.x.new_ones((b_data.x.shape[-2], 1)) if b_data.weights is None else b_data.weights
        for i in range(self.l_n):
            h = b_data.x
            g = b_data.edge_index
            h = self.down_gmps[i](h, g)
            # record the infor before aggregation
            down_outs.append(h)
            down_gs.append(g)
            batches.append(b_data.batch)
            ws.append(b_data.weights)
            

            # aggregate then pooling
            # Calculates edge and node weigths
            if self.args.edge_conv:
                ew, w = self.edge_conv.cal_ew(b_data.weights, g)
                b_data.weights = w
                # Does edge convolution on nodes with edge weigths
                h = self.edge_conv(h, g, ew)
                # Does edge convolution on position with edge weights
                cts.append(ew)
            b_data.x = h
            if self.args.pool_strat == "ASA":
                x, edge_index, edge_weight, batch, index = self.pools[i](
                    b_data.x, b_data.edge_index, b_data.edge_weight, b_data.batch
                )
                down_masks.append(index)
                b_data.x = x
                b_data.edge_index = edge_index
                b_data.edge_weight = edge_weight
                b_data.batch = batch
                b_data.weights = b_data.weights[index]
            else:
                x, edge_index, edge_weight, batch, index, _ = self.pools[i](
                    b_data.x, b_data.edge_index, b_data.edge_weight, b_data.batch
                )
                down_masks.append(index)
                b_data.x = x
                b_data.edge_index = edge_index
                b_data.edge_weight = edge_weight
                b_data.batch = batch
                b_data.weights = b_data.weights[index]
        b_data.x = self.bottom_gmp(b_data.x, b_data.edge_index)
        for i in range(self.l_n):
            up_idx = self.l_n - i - 1
            h = self.unpools[i](
                b_data.x, down_outs[up_idx].shape[0], down_masks[up_idx]
            )
            tmp_g = down_gs[up_idx]
            if self.args.edge_conv:
                h = self.edge_conv(h, tmp_g, cts[up_idx], aggragating=False)
            h = self.up_gmps[i](h, g)
            h = h.add(down_outs[up_idx])
            b_data.x = h
            b_data.edge_index = tmp_g
            b_data.batch = batches[up_idx]
            b_data.weights = ws[up_idx]
        b_data.edge_attr = edge_attr
        return b_data
    
    def _pooling_strategy(self):
        if self.args.pool_strat == "ASA":
            pool = ASAPooling
        elif self.args.pool_strat == "SAG":
            pool = SAGPooling
        else:
            pool = TopKPooling
        return pool

class ProcessorLayer(MessagePassing):
    def __init__(self, in_channels, out_channels,  **kwargs):
        super(ProcessorLayer, self).__init__(  **kwargs )
        """
        in_channels: dim of node embeddings [128], out_channels: dim of edge embeddings [128]

        """

        # Note that the node and edge encoders both have the same hidden dimension
        # size. This means that the input of the edge processor will always be
        # three times the specified hidden dimension
        # (input: adjacent node embeddings and self embeddings)
        self.edge_mlp = Sequential(Linear( 3* in_channels , out_channels),
                                   ReLU(),
                                   Linear( out_channels, out_channels),
                                   LayerNorm(out_channels))

        self.node_mlp = Sequential(Linear(in_channels + out_channels, out_channels),
                                   ReLU(),
                                   Linear(out_channels, out_channels),
                                   LayerNorm(out_channels))


        self.reset_parameters()

    def reset_parameters(self):
        """
        reset parameters for stacked MLP layers
        """
        self.edge_mlp[0].reset_parameters()
        self.edge_mlp[2].reset_parameters()

        self.node_mlp[0].reset_parameters()
        self.node_mlp[2].reset_parameters()

    def forward(self, b_data, size = None):
        """
        Handle the pre and post-processing of node features/embeddings,
        as well as initiates message passing by calling the propagate function.

        Note that message passing and aggregation are handled by the propagate
        function, and the update

        x has shpae [node_num , in_channels] (node embeddings)
        edge_index: [2, edge_num]
        edge_attr: [edge_num, in_channels]

        """
        x = b_data.x
        edge_index = b_data.edge_index
        edge_attr = b_data.edge_attr

        out, updated_edges = self.propagate(edge_index, x = x, edge_attr = edge_attr, size = size) # out has the shape of [E, out_channels]
        updated_nodes = torch.cat([x,out],dim=1)        # Complete the aggregation through self-aggregation
        updated_nodes = self.node_mlp(updated_nodes) # residual connection
        b_data.x = updated_nodes
        b_data.edge_attr = updated_edges
        return b_data

    def message(self, x_i, x_j, edge_attr):
        """
        source_node: x_i has the shape of [E, in_channels]
        target_node: x_j has the shape of [E, in_channels]
        target_edge: edge_attr has the shape of [E, out_channels]

        The messages that are passed are the raw embeddings. These are not processed.
        """

        updated_edges=torch.cat([x_i, x_j, edge_attr], dim = 1) # tmp_emb has the shape of [E, 3 * in_channels]
        updated_edges=self.edge_mlp(updated_edges)

        return updated_edges

    def aggregate(self, updated_edges, edge_index, dim_size = None):
        """
        First we aggregate from neighbors (i.e., adjacent nodes) through concatenation,
        then we aggregate self message (from the edge itself). This is streamlined
        into one operation here.
        """

        # The axis along which to index number of nodes.
        node_dim = 0

        out = scatter(updated_edges, edge_index[0, :], dim=node_dim, reduce = 'sum')

        return out, updated_edges


class Unpool(nn.Module):
    """
    Fills an empty array
    """

    def __init__(self, *args):
        super(Unpool, self).__init__()

    def forward(self, h, pre_node_num, idx):
        new_h = h.new_zeros([pre_node_num, h.shape[-1]])
        new_h[idx] = h
        return new_h