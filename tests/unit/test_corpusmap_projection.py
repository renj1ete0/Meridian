"""The projection behind the corpus map (tasks P6-26, P6-29).

The map is only worth drawing if its geometry is the embeddings' and not the
algorithm's. So these check the properties a reader relies on without knowing
it: that planted structure comes out where it was put, that the same corpus
draws the same picture, and that degenerate input yields a quiet answer rather
than NaNs on a canvas.
"""

from __future__ import annotations

import numpy as np
import pytest

from meridian_core import corpusmap
from meridian_core.corpusmap import COMPONENTS, project

DIM = 64


def planted(n: int = 400, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Points spread along three known orthogonal directions, plus small noise.

    Returns the vectors and each point's true (a, b, c) coordinates, so the test
    can ask whether the projection found them rather than whether it produced
    numbers.
    """
    rng = np.random.default_rng(seed)
    axes = np.linalg.qr(rng.normal(size=(DIM, 3)))[0].T
    truth = rng.normal(size=(n, 3)) * np.array([5.0, 3.0, 1.5])
    vectors = truth @ axes + rng.normal(scale=0.05, size=(n, DIM))
    return vectors, truth


def test_planted_structure_comes_out_on_the_axes_it_was_put() -> None:
    vectors, truth = planted()
    coords, explained = project(vectors)

    # Up to sign and scale, which PCA does not fix and the map does not need.
    for axis in range(COMPONENTS):
        found = abs(np.corrcoef(coords[:, axis], truth[:, axis])[0, 1])
        assert found > 0.99, f"axis {axis} did not recover its planted direction"
    # Axes come out in order of spread, and three explain nearly all of it.
    assert explained[0] > explained[1] > explained[2] > 0
    assert sum(explained) > 0.95


def test_the_third_axis_is_not_a_copy_of_the_first_two() -> None:
    """A third column that merely echoed an earlier axis (an index slip, a
    basis that never orthogonalised) would still land in [-1, 1] and pass every
    range check. Principal components are uncorrelated by construction."""
    coords, _ = project(planted()[0])
    correlation = np.corrcoef(coords.T)
    off_diagonal = correlation[~np.eye(COMPONENTS, dtype=bool)]
    assert np.abs(off_diagonal).max() < 0.05


def test_coordinates_fill_the_unit_cube_exactly() -> None:
    """Scaled so the widest point on each axis sits at ±1 — the view's edge."""
    coords, _ = project(planted()[0])
    assert coords.shape == (400, COMPONENTS)
    assert np.allclose(np.abs(coords).max(axis=0), 1.0)


def test_the_same_corpus_draws_the_same_map() -> None:
    """Row order must not flip the picture. An eigenvector's sign is arbitrary,
    and without fixing it a reshuffled sample renders as a mirror image."""
    vectors, _ = planted()
    order = np.random.default_rng(1).permutation(len(vectors))

    coords, _ = project(vectors)
    shuffled, _ = project(vectors[order])

    assert np.allclose(coords[order], shuffled, atol=1e-6)


@pytest.mark.parametrize("flip", [(-1, 1, 1), (1, -1, 1), (1, 1, -1), (-1, -1, -1)])
def test_every_axis_has_its_sign_fixed(
    monkeypatch: pytest.MonkeyPatch, flip: tuple[int, int, int]
) -> None:
    """An eigenvector is only defined up to sign, and which sign a solver
    returns is its own business — it can change with the library version, the
    BLAS underneath, or the starting block. So the solver is made to return the
    other sign, axis by axis, and the map must not notice. The third axis is
    the one a rule written for two columns would leave mirrored."""
    vectors, _ = planted()
    reference, _ = project(vectors)

    exact = np.linalg.eigh

    def contrary(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        values, vecs = exact(matrix)
        order = np.argsort(values)[::-1]
        signs = np.ones(len(values))
        signs[order[: len(flip)]] = flip
        return values, vecs * signs

    monkeypatch.setattr(corpusmap.np.linalg, "eigh", contrary)
    flipped, _ = project(vectors)

    assert np.allclose(reference, flipped, atol=1e-6)


@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_too_few_points_to_define_three_axes_sit_at_the_origin(n: int) -> None:
    coords, explained = project(np.random.default_rng(n).normal(size=(n, DIM)))
    assert coords.shape == (n, COMPONENTS)
    assert not coords.any()
    assert explained == (0.0, 0.0, 0.0)


def test_fewer_dimensions_than_axes_leaves_the_spare_axis_empty() -> None:
    """Two-dimensional input has no third direction. The third column is zero
    and its share is zero, rather than an index error or an axis of noise."""
    rng = np.random.default_rng(3)
    coords, explained = project(rng.normal(size=(50, 2)) * np.array([3.0, 1.0]))
    assert coords.shape == (50, COMPONENTS)
    assert not coords[:, 2].any()
    assert explained[2] == 0.0
    assert explained[0] + explained[1] == pytest.approx(1.0)


def test_identical_vectors_give_zeros_not_nans() -> None:
    """No variance at all: every division in the projection has a zero under it."""
    coords, explained = project(np.ones((10, DIM)))
    assert np.isfinite(coords).all()
    assert not coords.any()
    assert explained == (0.0, 0.0, 0.0)


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
    order = np.argsort(values)[::-1][:COMPONENTS]
    exact = centred @ vecs[:, order]

    for axis in range(COMPONENTS):
        assert abs(np.corrcoef(coords[:, axis], exact[:, axis])[0, 1]) > 0.999
    assert np.allclose(explained, values[order] / values.sum(), rtol=1e-3)
