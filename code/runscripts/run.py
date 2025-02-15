#!/usr/bin/env python3
"""Main file for running setup, training and testing"""
import argparse
import copy
import random
import sys

import numpy as np
import torch
from loguru import logger
from sklearn.model_selection import ParameterGrid, train_test_split
from torch_geometric.loader import DataLoader

sys.path.append("../")
sys.path.append("dataprocessing")
sys.path.append("model")
sys.path.append("utils")
from datetime import datetime

from dataprocessing.dataset import MeshDataset
from model.model import MultiScaleAutoEncoder
from utils.helperfuncs import (
    decode_and_save_set,
    encode_and_save_set,
    fetch_random_args,
    load_args,
    load_model,
    merge_dataset_stats,
    print_args,
    save_graph_structure,
    save_loss_ts_as_np,
)
from utils.parserfuncs import none_or_float, none_or_int, none_or_str, t_or_f

sys.path.append("../")
sys.path.append("dataprocessing")
sys.path.append("model")
sys.path.append("utils")
from train import loss_over_t, train
from utils.visualization import make_gif, plot_test_loss, save_plot


def apply_transform(args):
    """Runs the model with transformation on data and then again without
    transformation to evaluate how it's doing. The function also sets the
    configuration s.t. it doesn't run model on transformed data twice"""
    logger.info("Applying Transformation")
    args.time_stamp += "_transform"
    main(args)
    logger.info("transform done")
    args.load_model = True
    args.model_file = f"model_{args.time_stamp}.pt"
    args.transform = False
    args.time_stamp += "_post_transform"
    return args


day = datetime.now().strftime("%d-%m-%y")
parser = argparse.ArgumentParser()
parser.add_argument("-ae_ratio", type=none_or_float, default=0.5)
parser.add_argument("-ae_layers", type=int, default=3)
parser.add_argument("-alpha", type=float, default=0.5)
parser.add_argument("-batch_size", type=int, default=2)
parser.add_argument("-batch_norm", type=t_or_f, default=True)
parser.add_argument("-args_file", type=none_or_str, default=None)
parser.add_argument(
    "-data_dir", type=str, default="../data/cylinder_flow/trajectories_1768"
)
parser.add_argument("-dual_loss", type=t_or_f, default=False)
parser.add_argument("-epochs", type=int, default=40)
parser.add_argument("-edge_conv", type=t_or_f, default=True)
parser.add_argument("-hidden_dim", type=int, default=32)
parser.add_argument("-instance_id", type=int, default=935)
parser.add_argument("-latent_space", type=t_or_f, default=True)
parser.add_argument("-logger_lvl", type=str, default="INFO")
parser.add_argument("-loss", type=none_or_str, default="LMSE")
parser.add_argument("-masked_loss", type=t_or_f, default=True)
parser.add_argument("-load_model", type=t_or_f, default=True)
parser.add_argument("-loss_step", type=int, default=10)
parser.add_argument("-log_step", type=int, default=10)
parser.add_argument("-latent_dim", type=int, default=64)
parser.add_argument("-lr", type=float, default=1e-4)
parser.add_argument("-make_gif", type=t_or_f, default=False)
parser.add_argument("-max_latent_nodes", type=int, default=0)
parser.add_argument("-max_latent_edges", type=int, default=0)
parser.add_argument("-model_file", type=str, default="model.pt")
parser.add_argument("-mpl_ratio", type=float, default=0.3)
parser.add_argument("-mpl_layers", type=int, default=2)
parser.add_argument("-normalize", type=t_or_f, default=False)
parser.add_argument("-num_blocks", type=int, default=2)
parser.add_argument("-num_workers", type=int, default=1)
parser.add_argument("-n_nodes", type=int, default=0)
parser.add_argument("-opt", type=str, default="adam")
parser.add_argument("-out_feature_dim", type=none_or_int, default=54)
parser.add_argument("-one_traj", type=t_or_f, default=True)
parser.add_argument("-pool_strat", type=str, default="TopK")
parser.add_argument("-progress_bar", type=t_or_f, default=False)
parser.add_argument("-pretext_task", type=t_or_f, default=False)
parser.add_argument("-random_search", type=t_or_f, default=False)
parser.add_argument("-residual", type=t_or_f, default=True)
parser.add_argument("-save_args_dir", type=str, default="../logs/args/" + day)
parser.add_argument("-save_accuracy_dir", type=str, default="../logs/accuracies/" + day)
parser.add_argument(
    "-graph_structure_dir", type=str, default="../logs/graph_structure/"
)
parser.add_argument("-save_gif_dir", type=str, default="../logs/gifs/" + day)
parser.add_argument(
    "-save_loss_over_t_dir", type=str, default="../logs/loss_over_t/" + day
)
parser.add_argument("-save_mesh_dir", type=str, default="../logs/meshes/" + day)
parser.add_argument(
    "-save_model_dir", type=str, default="../logs/model_chkpoints/" + day
)
parser.add_argument(
    "-save_visualize_dir", type=str, default="../logs/visualizations/" + day
)
parser.add_argument("-shuffle", type=t_or_f, default=True)
parser.add_argument("-save_encodings", type=t_or_f, default=False)
parser.add_argument("-save_plot", type=t_or_f, default=True)
parser.add_argument("-save_model", type=t_or_f, default=True)
parser.add_argument("-save_latent", type=t_or_f, default=False)
parser.add_argument("-save_visual", type=t_or_f, default=True)
parser.add_argument("-save_losses", type=t_or_f, default=True)
parser.add_argument("-save_mesh", type=t_or_f, default=True)
parser.add_argument("-save_plot_dir", type=str, default="../logs/plots/" + day)
parser.add_argument("-train", type=t_or_f, default=True)
parser.add_argument("-train_model", type=t_or_f, default=True)
parser.add_argument(
    "-time_stamp", type=none_or_str, default=datetime.now().strftime("%H.%M-%d_%m_%Y")
)
parser.add_argument("-test_ratio", type=float, default=0.2)
parser.add_argument("-val_ratio", type=float, default=0.1)
parser.add_argument("-weight_decay", type=float, default=0.0005)
args = parser.parse_args()
args.day = day


def main(args):
    # args.transform = 'Attribute'
    # To ensure reproducibility the best we can, here we control the sources of
    # randomness by seeding the various random number generators used in this Colab
    # For more information, see:
    # https://pytorch.org/docs/stable/notes/randomness.html

    # Tested to be a decent seed
    seed = 5
    torch.manual_seed(seed)  # Torch
    random.seed(seed)  # Python
    np.random.seed(seed)  # NumPy
    args.device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info(f"Device : {args.device}")
    # Loads args if args.arg_file != None
    if args.load_model:
        args = load_args(args)
    # Initialize dataset, containing one trajecotry.
    # NOTE: This will be changed to only take <args>
    train_data = MeshDataset(args=args, mode="train")
    test_data = MeshDataset(args=args, mode="test")
    val_data = MeshDataset(args=args, mode="val")
    args.in_dim_node, args.in_dim_edge, args.n_nodes = (
        train_data[0].num_features,
        train_data[0].edge_attr.shape[1],
        train_data[0].x.shape[0],
    )
    (
        m_ids,
        m_gs,
        e_s,
        m_pos,
        args.max_latent_nodes,
        args.max_latent_edges,
        graph_placeholders,
    ) = merge_dataset_stats(train_data, test_data, val_data)

    save_graph_structure(args, m_ids, m_gs, e_s, m_pos, graph_placeholders)

    # args.latent_vec_dim = math.ceil(dataset[0].num_nodes*(args.ae_ratio**args.ae_layers))
    # Initialize Model

    model = MultiScaleAutoEncoder(args, m_ids, m_gs, e_s, m_pos, graph_placeholders)
    model = model.to(args.device)
    if args.load_model:
        model = load_model(args, model)

    # dataset = copy.deepcopy(val_data[:250]) # The rest of the dataset have little variance
    # ================================
    # SPLIT DATASET INTO TEST AND TRAIN
    # ================================
    if args.one_traj:
        dataset = copy.deepcopy(val_data)

        train_data, test_data = train_test_split(
            dataset, test_size=args.test_ratio, random_state=seed
        )

        # Split training data into train and validation data
        train_data, val_data = train_test_split(
            train_data,
            test_size=args.val_ratio / (1 - args.test_ratio),
            random_state=seed,
        )
    val_data.sort(key=lambda g: g.t)

    logger.info(
        f"\n\tTrain size : {len(train_data)}, \n\
        Validation size : {len(val_data)}, \n\
        Test size : {len(test_data)}"
    )
    # Create Dataloaders for train, test and validation
    train_loader = DataLoader(
        train_data, batch_size=args.batch_size, shuffle=args.shuffle
    )
    val_loader = DataLoader(val_data, batch_size=1, shuffle=False)
    test_loader = DataLoader(test_data, batch_size=1, shuffle=False)
    logger.success("All data loaded")

    # TRAINING
    if not args.load_model:
        with torch.autograd.set_detect_anomaly(True):
            train_losses, validation_losses, model = train(
                model=model,
                train_loader=train_loader,
                val_loader=val_loader,
                args=args,
            )
        logger.success("Training done")
    if args.save_plot and not args.load_model:
        save_plot(args, train_losses, validation_losses)

    ts, loss_ts = loss_over_t(model, test_loader, args)
    save_loss_ts_as_np(args, ts, loss_ts)
    plot_test_loss(ts, loss_ts, args, PATH=args.save_loss_over_t_dir)

    # extend the list and sort it with regards to t
    train_data.extend(val_data)
    train_data.extend(test_data)
    test_data.sort(key=lambda g: g.t)
    if args.make_gif:
        make_gif(model, test_data[:80], args)
    if args.save_encodings:
        logger.info("Encoding graphs")
        encoder = model.encoder.to(args.device)
        # save_pair_encodings(args, encoder)
        encode_and_save_set(args, encoder, train_data)
    decode_and_save_set(args, model.decoder.to(args.device), train_data)
    # write_average_accuracy(args, loss_ts)


if __name__ == "__main__":
    # warnings.filterwarnings("ignore", ".*Sparse CSR tensor support is in beta state.*")
    logger.remove(0)
    logger.add(sys.stderr, level=args.logger_lvl.upper())
    logger.info(f"CUDA is available: {torch.cuda.is_available()}")
    logger.info(f"CUDA has version: {torch.version.cuda}")
    logger.debug(print_args(args))

    if not args.random_search:
        # run the model with the applied args
        args.time_stamp += f"_{args.latent_dim=}{args.ae_layers=}{args.num_blocks=}{args.hidden_dim=}{args.batch_size=}"
        main(args)

    else:
        # Define the parameters used during random search
        param_grid = {"num_blocks": [2, 3]}
        # param_grid = {"ae_layers": [1, 2, 3]}

        # param_grid = {"latent_dim": [16,32]}
        lst = list(ParameterGrid(param_grid))

        while len(lst) > 0:
            args, lst = fetch_random_args(args, lst)
            if args.pretext_task:
                args = apply_transform(args)
            logger.info(f"Doing the following config: {args.time_stamp}")
            try:
                main(args)
            except (ValueError, RuntimeError):
                print("ValueError, something is NaN")
                continue
            logger.success("Done")
    logger.success("process_done")
