"""
    This file contains the classes used to build our Multi Scale Auto Encoder GNN.
"""

import torch
from loguru import logger
from model.decoder import Decoder
from model.encoder import Encoder
from torch import nn


class MultiScaleAutoEncoder(nn.Module):
    """
    Multiscale Auto Encoder consist of n_layer of Message Passing Layers (MPL) with
    pooling and unpooling operations in between in order to obtain a coarse latent
    representation of a graph. Uses an Multilayer Perceptron (MLP) to compute node and
    edge features.
    Encode: G_0 -> MLP -> MPL -> TopKPool ... MPL -> G_l -> Z_l
    Decode: G_l -> MPL -> Unpool .... -> MPL -> MLP -> G'_0 ->
    """

    def __init__(self, args, m_ids, m_gs, e_s, m_pos, graph_placeholder):
        super().__init__()
        self.args = args
        self.encoder = Encoder(args, m_ids, m_gs)
        self.decoder = Decoder(args, m_ids, m_gs, e_s, m_pos, graph_placeholder)

    def forward(self, b_data, Train=True):
        kl, latent_vec, b_data = self.encoder(b_data, Train)
        if torch.any(torch.isnan(b_data.x)):
            logger.error("something is nan after encoder")
            exit()
        b_data = self.decoder(latent_vec)
        assert not torch.any(torch.isnan(b_data.x)), "something is nan after decoder"
        return b_data, kl
