#! /usr/bin/env python

from lab import tools
from lab.parser import Parser


def process_unsolvable(content, props):
    if props.get("exhausted", 0) or props.get("initial_pruned", 0):
        props["unsolvable"] = 1
    else:
        props["unsolvable"] = 0


def process_invalid(content, props):
    props["invalid"] = int("invalid" in props)


def process_memory_mb(content, props):
    if "peak_memory_usage_kb" in props:
        props["memory_mb"] = props["peak_memory_usage_kb"] / 1000


def add_coverage(content, props):
    if "cost" in props or props.get("unsolvable", 0):
        props["coverage"] = 1
    else:
        props["coverage"] = 0


def add_search_time_ms_per_expanded(context, props):
    if "search_time_s" in props:
        if props["num_expanded"] > 0:
            props["search_time_ms_per_expanded"] = (
                props["search_time_s"] * 1_000
            ) / props["num_expanded"]


def compute_total_time_s(content, props):
    # total_time is translation_time + search_time
    if "translation_time_s" in props and "search_time_s" in props:
        props["total_time_s"] = props["translation_time_s"] + props["search_time_s"]


def make_add_score_peak_memory_usage_bytes(max_memory_bytes: int):
    def add_scores(content, props):

        if "peak_memory_usage_kb" not in props:
            props[f"score_peak_memory_usage_bytes"] = 0
            return

        success = props["coverage"] or props["unsolvable"]

        props[f"score_peak_memory_usage_bytes"] = tools.compute_log_score(
            success,
            props.get("peak_memory_usage_kb") * 1_000,
            lower_bound=2_000_000,
            upper_bound=max_memory_bytes,
        )

    return add_scores


def out_of_memory(content, props):
    props["out_of_memory"] = int(
        props["out_of_time"] == 0
        and props["coverage"] == 0
        and props["unsolvable"] == 0
    )


def out_of_time(content, props):
    props["out_of_time"] = int("timed_out" in props)


class SearchParser(Parser):
    """
    Goal found at: 0.00365
    Total time: 0.003652
    Total plan cost: 11
    Plan length: 11 step(s).
    Expanded 238 state(s).
    Reopened 0 state(s).
    Evaluated 0 state(s).
    Evaluations: 0 state(s).
    Generated 1070 state(s).
    Dead ends: 0 state(s).
    Pruned: 0 state(s).
    Expanded until last jump: 234 state(s).
    Reopened until last jump: 0 state(s).
    Evaluated until last jump: 0 state(s).
    Generated until last jump: 1052 state(s).
    Peak memory usage: 7856 kB
    Number of registered states: 253
    Int hash set load factor: 253/256 = 0.988281
    Int hash set resizes: 8
    Solution found.
    Iteration finished correctly.
    """

    def __init__(self, max_memory_bytes: int):
        super().__init__()
        self.add_pattern(
            "translation_time_s", r"Total translation time: (.+)s", type=float
        )
        self.add_pattern(
            "search_time_s", r"Total time: (.+)", type=float
        )  # search_time is total time in powerlifted
        self.add_pattern("num_expanded", r"Expanded (\d+) state\(s\).", type=int)
        self.add_pattern("num_generated", r"Generated (\d+) state\(s\).", type=int)
        self.add_pattern(
            "num_expanded_until_last_g_layer",
            r"Expanded until last jump: (\d+) state\(s\).",
            type=int,
        )
        self.add_pattern(
            "num_generated_until_last_g_layer",
            r"Generated until last jump: (\d+) state\(s\).",
            type=int,
        )  # ok
        self.add_pattern("cost", r"Total plan cost: (\d+)", type=int)
        self.add_pattern("length", r"Plan length: (\d+) step\(s\).", type=int)
        self.add_pattern("initial_h_value", r"Initial heuristic value (\d+)", type=int)
        self.add_pattern("initial_pruned", r"(Initial state is unsolvable!)", type=str)
        self.add_pattern("exhausted", r"(No solution found!)", type=str)
        self.add_pattern("invalid", r"(Plan invalid)", type=str)
        self.add_pattern(
            "peak_memory_usage_kb", r"Peak memory usage: (\d+) kB", type=int
        )

        self.add_pattern(
            "timed_out", r".*(timed out after \d+ seconds).*", type=str, file="run.log"
        )
        self.add_function(process_unsolvable)
        self.add_function(process_invalid)
        self.add_function(process_memory_mb)
        self.add_function(add_coverage)
        self.add_function(out_of_time)
        self.add_function(out_of_memory)
        self.add_function(
            compute_total_time_s
        )  # has to come before translating search_time to ms
        self.add_function(add_search_time_ms_per_expanded)
        self.add_function(make_add_score_peak_memory_usage_bytes(max_memory_bytes))
