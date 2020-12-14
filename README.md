# Hierarchical DARTS - Neural Architecture Search (NAS)

## About

This is a repository for our work for CS 269 Research Seminar on Efficient Machine Learning taught by Professor Baharan Mirzasoleiman

This repository contains an implementation of Hierarchical DARTS (HDARTS) - a novel algorithm that combines the ideas of hierarchical search spaces for NAS from [Hierarchical Representations for Efficient Architecture Search](https://arxiv.org/abs/1711.00436) and differentiable architecture search from [DARTS](https://arxiv.org/abs/1806.09055).

The mathematical notation and the actual algorithm for HDARTS is detailed here at https://www.overleaf.com/project/5f9fdb9eacb45b000164049e.

## Design

- Alpha - class that encapsulates the architecture parameters
- Model - class that is used to instantiate a network from a given Alpha and Primitives

## Results

| Dataset  | Validation Accuracy  |  GPU Time (Hours) |
|---|---|---|
| MNIST | 91%  |  1.5 |

# Top Tasks

TODO: Load model from checkpoint and continue training 

TODO: Apply max to get final operations

TODO: Visualize operations learnt

TODO: Learning rate scheduler

TODO: Change initial weight on Zero operation to encourage sparsity

