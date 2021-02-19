import numpy as np
import pytest
import xarray as xr

from gcm_filters import Filter, FilterShape, GridType
from gcm_filters.filter import FilterSpec


tripolar_u_grids = [
    member
    for name, member in GridType.__members__.items()
    if name == "POP_SIMPLE_TRIPOLAR_U_GRID"
]


def _check_equal_filter_spec(spec1, spec2):
    assert spec1.n_lap_steps == spec2.n_lap_steps
    assert spec1.n_bih_steps == spec2.n_bih_steps
    np.testing.assert_allclose(spec1.s_l, spec2.s_l)
    np.testing.assert_allclose(spec1.s_b, spec2.s_b)


def _fold_northern_boundary(ufield, nx, invert):
    """Auxiliary function to create data on tripolar grid. """
    folded = ufield[-1, :]  # grab northernmost row
    folded = folded[::-1]  # mirror it
    if invert:
        folded = -folded
    folded = np.roll(folded, -1)  # shift by 1 cell to the left
    ufield[-1, 0 : nx // 2] = folded[0 : nx // 2]
    ufield[-1, nx // 2 - 1] = 0  # pivot point (first Arctic singularity) is on land
    ufield[-1, -1] = 0  # second Arctic singularity is on land too
    return ufield


# These values were just hard copied from my dev environment.
# All they do is check that the results match what I got when I ran the code.
# They do NOT assure that the filter spec is correct.
@pytest.mark.parametrize(
    "filter_args, expected_filter_spec",
    [
        (
            dict(
                filter_scale=10.0,
                dx_min=1.0,
                filter_shape=FilterShape.GAUSSIAN,
                transition_width=np.pi,
                ndim=2,
                n_steps=4,
            ),
            FilterSpec(
                n_lap_steps=4,
                s_l=[2.56046256, 8.47349198, 15.22333438, 19.7392088],
                n_bih_steps=0,
                s_b=[],
            ),
        ),
        (
            dict(
                filter_scale=2.0,
                dx_min=1.0,
                filter_shape=FilterShape.TAPER,
                transition_width=np.pi,
                ndim=1,
            ),
            FilterSpec(
                n_lap_steps=1,
                s_l=[9.8696044],
                n_bih_steps=4,
                s_b=[
                    -0.74638043 - 1.24167777j,
                    3.06062496 - 3.94612205j,
                    7.80242999 - 3.18038659j,
                    9.81491354 - 0.44874939j,
                ],
            ),
        ),
    ],
)
def test_filter_spec(filter_args, expected_filter_spec):
    """This test just verifies that the filter specification looks as expected."""
    filter = Filter(**filter_args)
    _check_equal_filter_spec(filter.filter_spec, expected_filter_spec)
    # TODO: check other properties of filter_spec?


@pytest.fixture(scope="module", params=list(GridType))
def grid_type_and_input_ds(request):
    grid_type = request.param

    ny, nx = (128, 256)
    data = np.random.rand(ny, nx)

    grid_vars = {}

    if grid_type == GridType.CARTESIAN_WITH_LAND:
        mask_data = np.ones_like(data)
        mask_data[: (ny // 2), : (nx // 2)] = 0
        da_mask = xr.DataArray(mask_data, dims=["y", "x"])
        grid_vars = {"wet_mask": da_mask}
    if grid_type == GridType.IRREGULAR_CARTESIAN_WITH_LAND:
        mask_data = np.ones_like(data)
        mask_data[: (ny // 2), : (nx // 2)] = 0
        da_mask = xr.DataArray(mask_data, dims=["y", "x"])
        grid_data = np.ones_like(data)
        da_grid = xr.DataArray(grid_data, dims=["y", "x"])
        grid_vars = {
            "wet_mask": da_mask,
            "dxw": da_grid,
            "dyw": da_grid,
            "dxs": da_grid,
            "dys": da_grid,
            "area": da_grid,
        }
    if grid_type == GridType.POP_SIMPLE_TRIPOLAR_T_GRID:
        mask_data = np.ones_like(data)
        mask_data[: (ny // 2), : (nx // 2)] = 0
        mask_data[0, :] = 0  #  Antarctica
        da_mask = xr.DataArray(mask_data, dims=["y", "x"])
        grid_vars = {"wet_mask": da_mask}
    if grid_type == GridType.POP_SIMPLE_TRIPOLAR_U_GRID:
        data = _fold_northern_boundary(
            data, nx, invert=False
        )  # for now, we assume non-inverted velocities, otherwise testing for conservation is meaningless, see discussion in PR #26
        mask_data = np.ones_like(data)
        mask_data[: (ny // 2), : (nx // 2)] = 0
        mask_data[0, :] = 0  #  Antarctica
        mask_data = _fold_northern_boundary(mask_data, nx, invert=False)
        da_mask = xr.DataArray(mask_data, dims=["y", "x"])
        grid_vars = {"wet_mask": da_mask}

    da = xr.DataArray(data, dims=["y", "x"])

    return grid_type, da, grid_vars


@pytest.mark.parametrize(
    "filter_args",
    [dict(filter_scale=1.0, dx_min=1.0, n_steps=10, filter_shape=FilterShape.TAPER)],
)
def test_filter(grid_type_and_input_ds, filter_args):
    grid_type, da, grid_vars = grid_type_and_input_ds
    filter = Filter(grid_type=grid_type, grid_vars=grid_vars, **filter_args)
    filtered = filter.apply(da, dims=["y", "x"])

    # check conservation
    # this would need to be replaced by a proper area-weighted integral

    # the following test for tripolar_u_grids will still not be satisfied, despite what was discussed/concluded in #PR 26; have to figure out what we want to do for u fields on tripolar grids
    # if grid_type in tripolar_u_grids:
    #     # sum over left half of fold (to not double-count) + sum over remainder of domain
    #     nx = np.shape(da)[1]
    #     da_sum = da[-1,:nx//2].sum() + da[:-1,:].sum()
    #     filtered_sum = filtered[-1,:nx//2].sum() + filtered[:-1,:].sum()
    if grid_type not in tripolar_u_grids:
        da_sum = da.sum()
        filtered_sum = filtered.sum()

        xr.testing.assert_allclose(da_sum, filtered_sum)

    # check variance reduction
    assert (filtered ** 2).sum() < (da ** 2).sum()

    # check that we get an error if we leave out any required grid_vars
    for gv in grid_vars:
        grid_vars_missing = {k: v for k, v in grid_vars.items() if k != gv}
        with pytest.raises(ValueError, match=r"Provided `grid_vars` .*"):
            filter = Filter(
                grid_type=grid_type, grid_vars=grid_vars_missing, **filter_args
            )
