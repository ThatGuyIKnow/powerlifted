#! /usr/bin/env python
"""
Powerlifted external baseline on the HTG suite, matched to the distributed-tyr-c
paper's budget (10 minutes, 32 GiB).

Why this script exists
----------------------
The distributed-parallel-lifted-planning paper currently compares eight variants
of one engine (Tyr) against each other but has NO external anchor, so a reader
cannot tell whether the reported coverage is competitive with the state of the
art. The existing Powerlifted run (gbfs/data/2026-1-9-lazy-gbfs-ff*) is on the
standard IPC satisficing suite (64 domains, 1 problem each) with no memory/OOM
recorded, so it is not comparable. This script reproduces the Powerlifted
gbfs-lazy-hFF configuration on the SAME benchmark (htg-domains / SUITE_HTG, 1058
instances) under the SAME limits (10 min, 32 GiB), so #C / #M / #T / MS line up
with the paper's metrics:

  * coverage      -- a plan was found (the wrapper additionally runs VAL).
  * out_of_time   -- the run was wall-clock killed at the budget
                     ("timed out after N seconds" in run.log).
  * out_of_memory -- failed, not by timeout and not unsolvable (the residual
                     bucket; Powerlifted is killed by the rlimit on overrun).
  * memory_mb / score_peak_memory_usage_bytes -- from Powerlifted's
                     "Peak memory usage: N kB" line.

These are exactly the derivations in search_parser.py, so the Powerlifted column
is comparable to the paper's Table 1.

Run from the powerlifted repo root with the local uv/venv, e.g.

    DOWNWARD_BENCHMARKS=/path/to/benchmarks \
        uv run experiments/gbfs/2026-06-17-A-powerlifted-htg-10min.py build start parse fetch

Powerlifted is invoked directly as
`powerlifted.py -d <domain> -i <instance> -s lazy -e ff --plan-file plan`
(gbfs-lazy + FF), wrapped by the external memory monitor; build the search
binary first with `./build.py`. Coverage is taken from the planner's reported
plan -- pass `--validate` (with VAL on PATH) if VAL-verification is also wanted.
The non-REMOTE path is a small smoke subset only.
"""

import os
import platform
import re
import sys
from pathlib import Path

from downward import suites
from downward.reports.absolute import AbsoluteReport
from lab.environments import LocalEnvironment, SlurmEnvironment, TetralithEnvironment
from lab.experiment import Experiment

DIR = Path(__file__).resolve().parent
REPO = DIR.parent.parent

sys.path.append(str(DIR.parent))

from search_parser import SearchParser
from suite import SUITE_HTG


class BaseReport(AbsoluteReport):
    INFO_ATTRIBUTES = ["wall_time_limit", "memory_limit"]
    ERROR_ATTRIBUTES = ["domain", "problem", "algorithm", "unexplained_errors", "error", "node"]


class ArrheniusEnvironment(SlurmEnvironment):
    """Environment for the NAISS Arrhenius cluster."""

    DEFAULT_PARTITION = "cpu"
    DEFAULT_QOS = "normal"
    DEFAULT_MEMORY_PER_CPU = "3G"
    DEFAULT_TIME_LIMIT_PER_TASK = "24:00:00"
    MAX_TASKS = 1000
    # SLURM exports the submitting shell's environment by default (as on
    # Tetralith, which also leaves this empty), so loading modules before
    # `uv run ...` normally suffices. For a self-contained job, set e.g.:
    #   DEFAULT_SETUP = "module purge\nmodule load <toolchain>/<ver> <OpenMPI>/<ver>"
    # Verify names with `module avail` on Arrhenius (MPI + uv/Python).
    DEFAULT_SETUP = ""

    @classmethod
    def is_present(cls):
        node = platform.node()
        return bool(re.match(r"arrhenius\d+\.hpc\.arrhenius\.naiss\.se|n\d+", node))


BENCHMARKS_DIR = Path(os.environ["DOWNWARD_BENCHMARKS"])
NODE = platform.node()
REMOTE = re.match(
    r"tetralith\d+\.nsc\.liu\.se|arrhenius\d+\.hpc\.arrhenius\.naiss\.se|n\d+",
    NODE,
)

MEMORY_LIMIT = 32_000  # MB, matching the paper.

if REMOTE:
    EnvCls = ArrheniusEnvironment if ArrheniusEnvironment.is_present() else TetralithEnvironment
    if EnvCls is TetralithEnvironment:
        EnvCls.MAX_TASKS = 100
    ENV = EnvCls(
        setup=EnvCls.DEFAULT_SETUP,
        memory_per_cpu="2840M",
        cpus_per_task=16,  # 16 * 2840M >= 32 GiB, single rank
        extra_options="#SBATCH --account=naiss2025-22-1329-cpu",
    )
    SUITES = [("htg-domains", SUITE_HTG)]
    WALL_TIME_LIMIT = 10 * 60          # 10 minutes, matching the paper.
else:
    ENV = LocalEnvironment(processes=6)
    # Local wiring smoke test only: a couple of small HTG instances.
    SUITES = [("htg-domains", ["visitall-multidimensional-3-dim-visitall-CLOSE-g1"])]
    WALL_TIME_LIMIT = 30

ATTRIBUTES = [
    "run_dir",
    "coverage",
    "unsolvable",
    "invalid",
    "initial_h_value",
    "search_time_s",
    "total_time_s",
    "num_generated",
    "num_expanded",
    "cost",
    "length",
    "memory_mb",
    "score_peak_memory_usage_bytes",
    "out_of_time",
    "out_of_memory",
    "ext_peak_memory_mb",
    "ext_mem_sum_peak_mb",
    "ext_mem_num_procs",
    "ext_mem_steps",
    "ext_time_to_peak_ms",
]

exp = Experiment(environment=ENV)
exp.add_parser(SearchParser(MEMORY_LIMIT * 1_000_000))

# powerlifted.py run by its real path so it resolves its own builds/ and src/.
PLANNER = str(REPO / "powerlifted.py")
# External memory monitor wrapper. Symlinked so it resolves its real path and
# finds mem_monitor.py beside it in experiments/. Gives Powerlifted the same
# OOM-robust resident-memory peak + 100 MB timeline as the distributed runs, so
# its memory is comparable rather than relying on the binary's own peak line.
exp.add_resource("mem_wrap", str(DIR.parent / "run_with_mem_monitor.py"), symlink=True)
PYTHON_EXE = sys.executable

for prefix, SUITE in SUITES:
    for task in suites.build_suite(BENCHMARKS_DIR / prefix, SUITE):
        run = exp.add_run()
        run.add_resource("domain", task.domain_file, symlink=True)
        run.add_resource("problem", task.problem_file, symlink=True)
        run.add_command(
            "run_planner",
            # gbfs-lazy + FF (matching the distributed planner). -d is passed
            # explicitly rather than relying on domain auto-detection, which is
            # fragile under lab's symlinked run dir. powerlifted.py is run by its
            # real path so it resolves its own builds/ and src/. Wrapped by the
            # external memory monitor (--match search catches the C++ search
            # process even if it is not a direct descendant).
            [PYTHON_EXE, "{mem_wrap}", "--match", "search", "--",
             PLANNER, "-d", "{domain}", "-i", "{problem}",
             "-s", "lazy", "-e", "ff", "--plan-file", "plan"],
            wall_time_limit=WALL_TIME_LIMIT,
            memory_limit=MEMORY_LIMIT,
        )
        run.set_property("domain", task.domain)
        run.set_property("problem", task.problem)
        run.set_property("algorithm", "powerlifted-gbfs-lazy-hff")
        run.set_property("wall_time_limit", WALL_TIME_LIMIT)
        run.set_property("memory_limit", MEMORY_LIMIT)
        run.set_property("id", ["powerlifted-gbfs-lazy-hff", task.domain, task.problem])

exp.add_step("build", exp.build)
exp.add_step("start", exp.start_runs)
exp.add_step("parse", exp.parse)
exp.add_fetcher(name="fetch")
exp.add_report(BaseReport(attributes=ATTRIBUTES), outfile="report.html")
exp.run_steps()
