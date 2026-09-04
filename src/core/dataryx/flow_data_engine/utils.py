import os
from base64 import encodebytes
from collections.abc import Callable, Iterable
from typing import Any

import polars as pl
import requests

from core.configs import logger
from core.configs.settings import AVAILABLE_RAM, WORKER_URL
from core.dataryx.flow_data_engine.subprocess_operations import ExternalDfFetcher, Status
from core.utils.utils import standardize_col_dtype


def get_data_type(vals: Iterable[Any]):
    types = set(type(val) for val in vals)
    if len(types) == 1:
        return types.pop().__name__
    elif types == {float, int}:
        return "float"
    else:
        return "str"


def calculate_schema(lf: pl.LazyFrame) -> list[dict]:
    # Check if we should offload schema calculation to the worker
    from core.configs.settings import OFFLOAD_TO_WORKER
    if OFFLOAD_TO_WORKER:
        try:
            r = ExternalDfFetcher(lf=lf, operation_type="calculate_schema", wait_on_completion=False, flow_id=-1, node_id=-1)
            schema_stats: list[dict] = r.get_result()

            for schema_stat in schema_stats:
                schema_stat["pl_datatype"] = getattr(pl.datatypes, schema_stat["pl_datatype"])

            return schema_stats
        except Exception as e:
            logger.warning(f"Failed to calculate schema stats via worker: {e}. Falling back to local calculation.")

    # Local schema stats calculation (Standalone Flet app mode)
    # 1. Get column names and types from LazyFrame schema
    pl_schema = lf.collect_schema()
    
    # 2. Collect a sample (max 10k rows) to perform fast local stats calculation
    try:
        df = lf.limit(10000).collect()
    except Exception as e:
        logger.debug(f"Failed to collect sample with limit: {e}. Trying full collect.")
        try:
            df = lf.collect()
        except Exception as ex:
            logger.error(f"Failed to collect data for local schema calculation: {ex}")
            # Return minimal empty stats matching PlType structure
            df = pl.DataFrame([], schema=pl_schema)

    schema_stats = []
    for i, (col_name, pl_type) in enumerate(pl_schema.items()):
        col_stats = {
            "column_name": col_name,
            "col_index": i,
            "pl_datatype": pl_type,
            "count": df.height,
            "null_count": 0,
            "mean": "",
            "std": -1.0,
            "min": "",
            "max": "",
            "median": 0.0,
            "n_unique": -1,
            "examples": ""
        }
        
        if df.height > 0 and col_name in df.columns:
            series = df[col_name]
            col_stats["null_count"] = int(series.null_count())
            
            try:
                col_stats["n_unique"] = series.n_unique()
            except Exception:
                pass
                
            try:
                non_nulls = series.drop_nulls().head(3).to_list()
                col_stats["examples"] = ", ".join(str(x) for x in non_nulls)
            except Exception:
                pass

            if pl_type.is_numeric():
                try:
                    non_null_series = series.drop_nulls()
                    if len(non_null_series) > 0:
                        col_stats["min"] = str(non_null_series.min())
                        col_stats["max"] = str(non_null_series.max())
                        col_stats["mean"] = f"{non_null_series.mean():.4f}" if non_null_series.mean() is not None else ""
                        col_stats["median"] = float(non_null_series.median()) if non_null_series.median() is not None else 0.0
                        col_stats["std"] = float(non_null_series.std()) if non_null_series.std() is not None else -1.0
                except Exception:
                    pass
            elif pl_type in (pl.String, pl.Boolean):
                try:
                    non_null_series = series.drop_nulls()
                    if len(non_null_series) > 0:
                        col_stats["min"] = str(non_null_series.min())
                        col_stats["max"] = str(non_null_series.max())
                except Exception:
                    pass
                    
        schema_stats.append(col_stats)
        
    return schema_stats



def write_polars_frame(
    _df: pl.LazyFrame | pl.DataFrame, path: str, data_type: str = "parquet", estimated_size: int = 0
):
    is_lazy = isinstance(_df, pl.LazyFrame)
    logger.info("Caching data frame")
    if is_lazy:
        if estimated_size > 0:
            fit_memory = estimated_size / 1024 / 1000 / 1000 < AVAILABLE_RAM
            if fit_memory:
                _df = _df.collect()
                is_lazy = False

        # Not every format has a streaming LazyFrame.sink_* counterpart --
        # e.g. Excel only has DataFrame.write_excel, no LazyFrame.sink_excel.
        # getattr() on a missing sink_ method previously raised an uncaught
        # AttributeError here (outside the try/except below), crashing any
        # write whose format lacks a sink_ variant -- most commonly Excel,
        # and most commonly triggered since estimated_size is 0 (skipping
        # the fit_memory collect() above) for any non-passthrough pipeline
        # output, i.e. most real pipelines.
        if is_lazy and hasattr(_df, "sink_" + data_type):
            logger.info("Writing in memory efficient mode")
            write_method = getattr(_df, "sink_" + data_type)
            try:
                write_method(path)
                return True
            except Exception:
                pass
        if is_lazy:
            _df = _df.collect()
    try:
        write_method = getattr(_df, "write_" + data_type)
        write_method(path)
        return True
    except:
        return False


def collect(df: pl.LazyFrame, streamable: bool = True):
    try:
        return df.collect(engine="streaming" if streamable else "auto")
    except:
        return df.collect(engine="auto")


def cache_polars_frame_to_temp(_df: pl.LazyFrame | pl.DataFrame, tempdir: str = None) -> pl.LazyFrame:
    path = f"{tempdir}\\fl_file_{id(_df)}"
    result = write_polars_frame(_df, path)
    if result:
        df = pl.read_parquet(path)
        return df.lazy()
    else:
        raise Exception("Could not cache the data")


def define_pl_col_transformation(col_name: str, col_type: pl.DataType) -> pl.Expr:
    if col_type == pl.Datetime:
        return pl.col(col_name).str.to_datetime(strict=False)
    elif col_type == pl.Date:
        return pl.col(col_name).str.to_date(strict=False)
    else:
        return pl.col(col_name).cast(col_type, strict=False)


def execute_write_method(
    write_method: Callable,
    path: str,
    data_type: str = None,
    sheet_name: str = None,
    delimiter: str = None,
    write_mode: str = "create",
):
    if data_type == "excel":
        logger.info("Writing as excel file")
        write_method(path, worksheet=sheet_name)
    elif data_type == "csv":
        logger.info("Writing as csv file")
        if write_mode == "append":
            with open(path, "ab") as f:
                write_method(path=f, separator=delimiter, quote_style="always")
        else:
            write_method(path=path, separator=delimiter, quote_style="always")
    elif data_type == "parquet":
        logger.info("Writing as parquet file")
        write_method(path)


def write_output(
    _df: pl.LazyFrame,
    data_type: str,
    path: str,
    write_mode: str,
    sheet_name: str = None,
    delimiter: str = None,
    flow_id: int = -1,
    node_id: int | str = -1,
) -> Status:
    serializable_df = _df.serialize()
    r = requests.post(
        f"{WORKER_URL}/write_results/",
        json={
            "operation": encodebytes(serializable_df).decode(),
            "data_type": data_type,
            "path": path,
            "write_mode": write_mode,
            "sheet_name": sheet_name,
            "delimiter": delimiter,
            "dataryx_node_id": node_id,
            "dataryx_flow_id": flow_id,
        },
    )
    if r.ok:
        return Status(**r.json())
    else:
        raise Exception(f"Could not cache the data, {r.text}")


def local_write_output(
    _df: pl.LazyFrame | pl.DataFrame,
    data_type: str,
    path: str,
    write_mode: str,
    sheet_name: str = None,
    delimiter: str = None,
    flow_id: int = -1,
    node_id: int | str = -1,
):
    is_lazy = isinstance(_df, pl.LazyFrame)
    sink_method_str = "sink_" + data_type
    write_method_str = "write_" + data_type
    has_sink_method = hasattr(_df, sink_method_str)
    write_method = None
    if os.path.exists(path) and write_mode == "create":
        return None
    if has_sink_method and is_lazy:
        write_method = getattr(_df, "sink_" + data_type)
    elif not is_lazy or not has_sink_method:
        if is_lazy:
            _df = _df.collect()
        write_method = getattr(_df, write_method_str)
    if write_method is not None:
        execute_write_method(
            write_method,
            path=path,
            data_type=data_type,
            sheet_name=sheet_name,
            delimiter=delimiter,
            write_mode=write_mode,
        )


def create_pl_df_type_save(raw_data: Iterable[Iterable], orient: str = "row") -> pl.DataFrame:
    """
        orient : {'col', 'row'}, default None
        Whether to interpret two-dimensional data as columns or as rows. If None,
        the orientation is inferred by matching the columns and data dimensions. If
        this does not yield conclusive results, column orientation is used.
    :param raw_data: iterables with values
    :param orient:
    :return: polars dataframe
    """
    if orient == "row":
        raw_data = zip(*raw_data, strict=False)
    raw_data = [standardize_col_dtype(values) for values in raw_data]
    return pl.DataFrame(raw_data, orient="col")


def find_first_positions(lst: list[str]) -> dict[str, int]:
    first_positions: dict[str, int] = {}
    for i, value in enumerate(lst):
        if value not in first_positions:
            first_positions[value] = i
    return first_positions


def match_order(l: list[str], ref: list[str]) -> list[str]:
    ref_order = find_first_positions(ref)
    order = []
    for v in l:
        org_order = ref_order.get(v, float("inf"))
        order.append(org_order)
    return [v for _, v in sorted(zip(order, l, strict=False))]
