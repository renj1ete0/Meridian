"""The projection behind the corpus map (task P6-26).

The map is only worth drawing if its geometry is the embeddings' and not the
algorithm's. So these check the properties a reader relies on without knowing
it: that planted structure comes out where it was put, that the same corpus
draws the same picture, and that degenerate input yields a quiet answer rather
than NaNs on a canvas.
"""

from __future__ import annotations

import numpy as np
import pytest

from meridian_core.corpusmap import project

DIM = 64


def planted(n: int = 400, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Points spread along two known orthogonal directions, plus small noise.

    Returns the vectors and each point's true (a, b) coordinates, so the test
    can ask whether the projection found them rather than whether it produced
    numbers.
    """
    rng = np.random.default_rng(seed)
    axes = np.linalg.qr(rng.normal(size=(DIM, 2)))[0].T
    truth = rng.normal(size=(n, 2)) * np.array([5.0, 2.0])
    vectors = truth @ axes + rng.normal(scale=0.05, size=(n, DIM))
    return vectors, truth


def test_planted_structure_comes_out_on_the_axes_it_was_put() -> None:
    vectors, truth = planted()
    coords, explained = project(vectors)

    # Up to sign and scale, which PCA does not fix and the map does not need.
    first = abs(np.corrcoef(coords[:, 0], truth[:, 0])[0, 1])
    second = abs(np.corrcoef(coords[:, 1], truth[:, 1])[0, 1])
    assert first > 0.99 and second > 0.99
    # The larger spread is the first axis, and two axes explain nearly all of it.
    assert explained[0] > explained[1]
    assert sum(explained) > 0.95


def test_coordinates_fill_the_unit_square_exactly() -> None:
    """Scaled so the widest point on each axis sits at ±1 — the canvas's edge."""
    coords, _ = project(planted()[0])
    assert np.allclose(np.abs(coords).max(axis=0), 1.0)


def test_the_same_corpus_draws_the_same_map() -> None:
    """Row order must not flip the picture. An eigenvector's sign is arbitrary,
    and without fixing it a reshuffled sample renders as a mirror image."""
    vectors, _ = planted()
    order = np.random.default_rng(1).permutation(len(vectors))

    coords, _ = project(vectors)
    shuffled, _ = project(vectors[order])

    assert np.allclose(coords[order], shuffled, atol=1e-6)


@pytest.mark.parametrize("n", [0, 1, 2])
def test_too_few_points_to_define_two_axes_sit_at_the_origin(n: int) -> None:
    coords, explained = project(np.ones((n, DIM)))
    assert coords.shape == (n, 2)
    assert not coords.any()
    assert explained == (0.0, 0.0)


def test_identical_vectors_give_zeros_not_nans() -> None:
    """No variance at all: every division in the projection has a zero under it."""
    coords, explained = project(np.ones((10, DIM)))
    assert np.isfinite(coords).all()
    assert not coords.any()
    assert explained == (0.0, 0.0)


def test_the_fast_projection_agrees_with_the_exact_one() -> None:
    """Subspace iteration replaced a full eigendecomposition for speed (seven
    seconds per map at a thousand dimensions). This is the check that the swap
    kept the answer: same axes, same variance shares, on a spectrum that decays
    the way a text embedding's does rather than one built to be easy."""
    rng = np.random.default_rng(7)
    n, d = 1500, 256
    decay = 1.0 / np.arange(1, d + 1) ** 0.7
    vectors = (rng.normal(size=(n, d)) * decay) @ np.linalg.qr(rng.normal(size=(d, d)))[0]

    coords, explained = project(vectors)

    centred = vectors - vectors.mean(axis=0)
    values, vecs = np.linalg.eigh(centred.T @ centred / (n - 1))
    order = np.argsort(values)[::-1][:2]
    exact = centred @ vecs[:, order]

    for axis in (0, 1):
        assert abs(np.corrcoef(coords[:, axis], exact[:, axis])[0, 1]) > 0.999
    assert np.allclose(explained, values[order] / values.sum(), rtol=1e-3)
