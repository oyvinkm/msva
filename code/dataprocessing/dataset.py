#!/usr/bin/env python3
import os
import pickle
import re

import torch
from dataprocessing.utils.helper_pooling import generate_multi_layer_stride
from loguru import logger
from torch.nn import functional as F
from torch_geometric.data import Data, Dataset


class MeshDataset(Dataset):
    def __init__(self, args, mode):
        self.args = args
        self.data_dir = args.data_dir
        self.layer_num = args.ae_layers
        self.mode = mode
        if mode not in ["train", "test", "val"]:
            self.mode = "train"
        # gets data file
        self.data_file = os.path.join(self.data_dir, f"{self.mode}")
        self.mm_dir = os.path.join(self.data_dir, "mm_files/")
        if not os.path.exists(self.mm_dir):
            os.mkdir(self.mm_dir)
        # directory for storing processed datasets
        self.last_idx = 0
        # number of nodes
        self.n = None
        # For normalization, not implemented atm

        self.max_latent_nodes = 0
        self.max_latent_edges = 0
        self.trajectories = set(
            map(lambda str: re.search("\d+", str).group(), self.processed_file_names)
        )
        self.m_ids = [{} for _ in range(self.layer_num)]
        self.m_gs = [{} for _ in range(self.layer_num + 1)]
        self.e_s = [{} for _ in range(self.layer_num)]
        self.m_pos = [{} for _ in range(self.layer_num + 1)]
        self.graph_placeholders = {t: None for t in self.trajectories}
        self._get_bi_stride()
        super().__init__(self.data_dir)

    def _get_bi_stride(self):
        for t in self.trajectories:
            f = next(filter(lambda str: str.startswith(t), self.processed_file_names))
            g = torch.load(os.path.join(self.data_file, f))
            m_ids, m_gs, e_s = self._cal_multi_mesh(t, g)
            self.make_placeholder(g, m_ids, m_gs, t)
        logger.info("Loaded multi mesh for all trajectories")

    def len(self):
        return len(self.processed_file_names)

    def _get_pool(self):
        return self.m_ids, self.m_gs, self.e_s, self.m_pos

    @property
    def processed_file_names(self):
        return os.listdir(self.data_file)

    def get(self, idx):
        file = list(
            filter(
                lambda str: str.endswith(f"data_{idx}.pt"), self.processed_file_names
            )
        )[0]
        g = torch.load(os.path.join(self.data_file, file))
        g.x = F.normalize(g.x)
        return g  # (G, m_ids, m_gs, e_s) -> max m_ids

    def _get_pool(self):
        return self.m_ids, self.m_gs, self.e_s, self.m_pos

    def __next__(self):
        if self.last_idx == self.len() - 1:
            raise StopIteration
        else:
            self.last_idx += 1
            return self.get(self.last_idx)

    def __iter__(self):
        return self

    def make_placeholder(self, g, m_ids, m_gs, trajectory):
        # Data(x=[1768, 54], edge_index=[2, 10132], edge_attr=[10132, 3], y=[1768, 2], p=[1768, 1], cells=[3298, 3], weights=[1768, 1], mesh_pos=[1768, 2], t=598, trajectory='147')
        x = torch.zeros((len(m_ids[-1]), self.args.latent_dim))
        edge_index = m_gs[-1]
        edge_attr = g.edge_attr
        y = g.y
        p = g.p
        cells = g.cells
        weights = torch.ones((len(m_ids[-1]), 1))
        mesh_pos = g.mesh_pos
        for m_id in m_ids:
            mesh_pos = mesh_pos[m_id]
        trajectory = trajectory
        self.graph_placeholders[trajectory] = Data(
            x=x,
            edge_index=edge_index,
            edge_attr=edge_attr,
            y=y,
            p=p,
            cells=cells,
            weights=weights,
            mesh_pos=mesh_pos,
            t=0,
            trajectory=trajectory,
        )

    def _cal_multi_mesh(self, traj, g):
        mmfile = os.path.join(
            self.mm_dir, str(traj) + "_mmesh_layer_" + str(self.layer_num) + ".dat"
        )
        mmexist = os.path.isfile(mmfile)
        if not mmexist:
            logger.info(f"Calculating multi mesh for trajectory {traj}")
            edge_i = g.edge_index
            n = g.x.shape[0]
            m_gs, m_ids, e_s = generate_multi_layer_stride(
                edge_i, self.layer_num, n=n, pos_mesh=None
            )
            m_mesh = {"m_gs": m_gs, "m_ids": m_ids, "e_s": e_s}
            pickle.dump(m_mesh, open(mmfile, "wb"))
        else:
            m_mesh = pickle.load(open(mmfile, "rb"))
            m_gs, m_ids, e_s = m_mesh["m_gs"], m_mesh["m_ids"], m_mesh["e_s"]
        if len(m_ids[-1]) > self.max_latent_nodes:
            self.max_latent_nodes = len(m_ids[-1])
        if m_gs[-1].shape[-1] > self.max_latent_edges:
            self.max_latent_edges = m_gs[-1].shape[-1]
        mesh_pos = g.mesh_pos
        self.m_pos[0][str(traj)] = mesh_pos
        for i in range(len(m_ids)):
            mesh_pos = mesh_pos[m_ids[i]]
            self.m_pos[i + 1][str(traj)] = mesh_pos
            self.m_ids[i][str(traj)] = torch.tensor(m_ids[i])
        for j in range(len(m_gs)):
            self.m_gs[j][str(traj)] = m_gs[j]
        for k in range(len(e_s)):
            self.e_s[k][str(traj)] = torch.tensor(e_s[k])
        return m_ids, m_gs, e_s


class DatasetPairs(Dataset):
    def __init__(self, args):
        self.data_dir = args.data_dir
        self.instance_id = args.instance_id
        self.normalize = args.normalize
        self.layer_num = args.ae_layers
        # gets data file

        if args.train:
            self.data_file = os.path.join(
                self.data_dir, f"pairs/train_pair_{str(self.instance_id)}.pt"
            )
        else:
            self.data_file = os.path.join(
                self.data_dir, f"pairs/test_pair_{str(self.instance_id)}.pt"
            )
        self.mm_dir = os.path.join(self.data_dir, "mm_files/")
        if not os.path.exists(self.mm_dir):
            os.mkdir(self.mm_dir)
        # directory for storing processed datasets
        # self.mm_dir = os.path.join(self.data_dir, 'mm_files/')
        self.last_idx = 0
        # number of nodes
        self.n = None

        self.traj_data = torch.load(self.data_file)
        # self._cal_multi_mesh()
        super().__init__(self.data_dir)

    def len(self):
        return len(self.traj_data)

    def get(self, idx):
        z1, z2, z3 = self.traj_data[idx]
        return (z1, z2, z3)

    def __next__(self):
        if self.last_idx == self.len() - 1:
            raise StopIteration
        else:
            self.last_idx += 1
            return self.get(self.last_idx)

    def __iter__(self):
        return self

    def _cal_multi_mesh(self, traj, g):
        mmfile = os.path.join(
            self.mm_dir, str(traj) + "_mmesh_layer_" + str(self.layer_num) + ".dat"
        )
        mmexist = os.path.isfile(mmfile)
        if not mmexist:
            edge_i = g.edge_index
            n = g.x.shape[0][0]
            m_gs, m_ids, e_s = generate_multi_layer_stride(
                edge_i, self.layer_num, n=n, pos_mesh=None
            )
            m_mesh = {"m_gs": m_gs, "m_ids": m_ids, "e_s": e_s}
            pickle.dump(m_mesh, open(mmfile, "wb"))
        else:
            m_mesh = pickle.load(open(mmfile, "rb"))
            m_gs, m_ids, e_s = m_mesh["m_gs"], m_mesh["m_ids"], m_mesh["e_s"]

        self.m_ids = m_ids
        self.m_gs = m_gs
        self.e_s = e_s


class LatentVectorPairDataset(Dataset):
    def __init__(self, args):
        self.latent_data = self.get_dataset_pairs(args)
        self.last_idx = 0
        super().__init__(self.latent_data)

    def len(self):
        return len(self.latent_data)

    def __setitem__(self, k, v):
        self.latent_data[k] = v

    def __getitem__(self, idx):
        return self.latent_data[idx]

    def get(self, idx):
        input = self.latent_data[idx]
        return input

    def __next__(self):
        if self.last_idx == self.len() - 1:
            raise StopIteration
        else:
            self.last_idx += 1
            return self.get(self.last_idx)

    def __iter__(self):
        return self

    def get_dataset_pairs(self, args):
        str_splt = args.decoder_path.split("/")
        PATH = os.path.join(
            str_splt[0],
            "data",
            "latent_space",
            str_splt[3],
            str_splt[4],
            "encoded_dataset_pairs.pt",
        )

        assert os.path.isfile(PATH), f"encoded_dataset_pairs at {PATH=} doesn't exist"
        encoded_dataset_pairs = torch.load(PATH, map_location=torch.device(args.device))
        return encoded_dataset_pairs
