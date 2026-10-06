"""The history summary: a run's message log boiled down to chartable JSON.

Logs here are written through the real ``RunLog``, so the tests read exactly the
shape a run leaves behind, and the summary is checked against numbers worked out
by hand.
"""

import argparse
import json

from bazaar import __main__ as cli
from bazaar.dashboard.history import SUMMARY_FILE, run_dirs, summarize_session, write_summary
from bazaar.dashboard.history.__main__ import main as history_main
from bazaar.dashboard.history.files import read_rows
from bazaar.runlog import RunLog
from bazaar.validation import model
from bazaar.validation.commands import CommandBuilder
from tests import factories as f

OFFER = model.OfferStatus
ME = "P01"


class Log:
    """Writes a run's messages the way the client does, and hands back the rows."""

    def __init__(self, directory, run_id="run-1"):
        self.log = RunLog(directory)
        self.run_id = run_id
        self.commands = CommandBuilder(run_id)

    def state(self, tick, *, phase=model.Phase.PHASE_RUNNING, **overrides):
        state = f.state(run_id=self.run_id, tick=tick, phase=phase, self_station_id=ME, **overrides)
        self.log.message("received", model.ServerMessage(state=state))

    def offer(self, request_id, to="P02", give=(1, 0, 0), receive=(0, 1, 0)):
        message = self.commands.offer(request_id, to, model.Bundle.of(give), model.Bundle.of(receive), 9)
        self.log.message("sent", message)

    def accept(self, request_id, offer_id="offer-9"):
        self.log.message("sent", self.commands.accept(request_id, offer_id))

    def ready(self):
        self.log.message("sent", self.commands.ready(1))

    def result(self, request_id, code=model.ResultCode.RESULT_CODE_OK):
        result = f.result(request_id, run_id=self.run_id, code=code, ok=code is model.ResultCode.RESULT_CODE_OK)
        self.log.message("received", model.ServerMessage(result=result))

    def protocol_error(self, request_id, code=model.ControlCode.CONTROL_CODE_REQUEST_CAPACITY_EXCEEDED):
        error = model.ProtocolError(
            type=model.ProtocolErrorType.PROTOCOL_ERROR_TYPE_PROTOCOL_ERROR,
            protocol_version="2.0",
            run_id=self.run_id,
            request_id=request_id,
            code=code,
            close_session=False,
        )
        self.log.message("received", model.ServerMessage(protocol_error=error))


def trade(transaction_id, proposer, recipient, give, receive, tick):
    return model.Transaction(
        transaction_id=transaction_id,
        offer_id=f"offer-{transaction_id}",
        proposer_id=proposer,
        recipient_id=recipient,
        give=model.Bundle.of(give),
        receive=model.Bundle.of(receive),
        settled_tick=tick,
        settled_version=tick,
    )


def summary_of(directory, **kwargs):
    return json.loads(write_summary(directory, **kwargs).read_text())["runs"][0]


def finished_run(directory):
    """Three ticks, four commands, three trades (one not ours), one bad command."""
    log = Log(directory)
    log.state(0, observation=f.observation(station_id=ME, health=100, inventory=f.bundle(30, 30, 30)))
    log.ready()
    log.offer("req-1")
    log.offer("req-2", to="P03")
    log.accept("req-3")
    log.result("req-1")
    log.result("req-2", model.ResultCode.RESULT_CODE_NOT_OPEN)
    log.protocol_error("req-3")
    log.state(1, observation=f.observation(station_id=ME, health=90, inventory=f.bundle(29, 30, 30)))
    log.offer("req-4")
    # The last state in tick 2 is the one that should be charted.
    log.state(2, observation=f.observation(station_id=ME, health=80, inventory=f.bundle(1, 1, 1)))
    log.state(
        2,
        phase=model.Phase.PHASE_FINISHED,
        outcome=model.PlayerOutcome(collective_success=True, self_failed=False, aborted=False),
        observation=f.observation(
            station_id=ME,
            health=70,
            inventory=f.bundle(2, 3, 4),
            last_unmet_upkeep=f.bundle(0, 1, 0),
            shortage_ticks=1,
            imported_total=f.bundle(1, 0, 3),
            exported_total=f.bundle(2, 0, 0),
        ),
        offers=[
            f.offer("o1", proposer_id=ME, recipient_id="P02", status=OFFER.OFFER_STATUS_ACCEPTED),
            f.offer("o2", proposer_id=ME, recipient_id="P03", status=OFFER.OFFER_STATUS_EXPIRED),
            f.offer("o3", proposer_id="P02", recipient_id=ME, status=OFFER.OFFER_STATUS_ACCEPTED),
        ],
        transactions=[
            trade("t1", ME, "P02", give=(2, 0, 0), receive=(0, 1, 0), tick=1),  # we proposed
            trade("t2", "P02", ME, give=(0, 0, 3), receive=(1, 0, 0), tick=2),  # we accepted
            trade("t3", "P02", "P03", give=(9, 9, 9), receive=(9, 9, 9), tick=2),  # not ours
        ],
    )
    return directory


# --- how the run ended ------------------------------------------------------

def test_a_finished_run_reports_its_outcome(tmp_path):
    run = summary_of(finished_run(tmp_path))["result"]
    assert run["status"] == "finished"
    assert run["collective_success"] is True
    assert run["our_station_failed"] is False
    assert (run["first_tick"], run["last_tick"], run["progress"]) == (0, 2, 0.02)
    assert run["phases_seen"] == ["RUNNING", "FINISHED"]


def test_a_log_that_just_stops_is_interrupted_and_says_how_far_it_got(tmp_path):
    log = Log(tmp_path)
    log.state(0)
    log.state(40)
    result = summary_of(tmp_path)["result"]
    assert result["status"] == "interrupted"
    assert result["last_tick"] == 40
    assert result["progress"] == 0.4
    assert result["outcome"] is None


def test_an_aborted_run_is_reported_as_aborted(tmp_path):
    Log(tmp_path).state(
        5,
        phase=model.Phase.PHASE_ABORTED,
        outcome=model.PlayerOutcome(collective_success=None, self_failed=False, aborted=True),
    )
    assert summary_of(tmp_path)["result"]["status"] == "aborted"


def test_a_station_that_failed_is_marked_even_in_a_run_that_was_cut_short(tmp_path):
    Log(tmp_path).state(3, observation=f.observation(station_id=ME, health=0, failed_once=True, first_failure_tick=2))
    run = summary_of(tmp_path)
    assert run["result"]["status"] == "interrupted"
    assert run["result"]["our_station_failed"] is True
    assert run["health"]["first_failure_tick"] == 2


# --- what the run owns ------------------------------------------------------

def test_trades_are_counted_from_our_side_and_other_peoples_trades_are_left_out(tmp_path):
    trades = summary_of(finished_run(tmp_path))["trades"]
    assert trades["count"] == 2
    assert (trades["we_proposed"], trades["we_accepted"]) == (1, 1)
    assert trades["gave"] == {"water": 3, "food": 0, "components": 0}  # 2 paid, then 1 paid as recipient
    assert trades["got"] == {"water": 0, "food": 1, "components": 3}
    assert trades["net"] == {"water": -3, "food": 1, "components": 3}
    [peer] = trades["by_counterparty"]
    assert (peer["station"], peer["count"]) == ("P02", 2)
    assert peer["net"] == trades["net"]


def test_counterparties_are_listed_busiest_first_then_by_station(tmp_path):
    Log(tmp_path).state(
        1,
        transactions=[
            trade("t1", ME, "P04", (1, 0, 0), (0, 1, 0), 1),
            trade("t2", ME, "P03", (1, 0, 0), (0, 1, 0), 1),
            trade("t3", ME, "P03", (1, 0, 0), (0, 1, 0), 1),
            trade("t4", ME, "P02", (1, 0, 0), (0, 1, 0), 1),
        ],
    )
    peers = summary_of(tmp_path)["trades"]["by_counterparty"]
    assert [(p["station"], p["count"]) for p in peers] == [("P03", 2), ("P02", 1), ("P04", 1)]


def test_only_offers_we_made_count_toward_the_acceptance_rate(tmp_path):
    offers = summary_of(finished_run(tmp_path))["offers"]
    assert offers == {"made": 2, "by_status": {"accepted": 1, "expired": 1}, "acceptance_rate": 0.5}


def test_totals_and_health_come_from_the_last_state(tmp_path):
    run = summary_of(finished_run(tmp_path))
    assert run["totals"]["imported"] == {"water": 1, "food": 0, "components": 3}
    assert run["totals"]["exported"] == {"water": 2, "food": 0, "components": 0}
    assert run["health"]["final"] == 70
    assert run["health"]["min"] == 70
    assert run["health"]["shortage_ticks"] == 1


# --- what the run did -------------------------------------------------------

def test_commands_are_matched_to_their_results_by_request_id(tmp_path):
    commands = summary_of(finished_run(tmp_path))["commands"]
    assert commands["sent"] == 4
    assert commands["by_kind"]["offer"] == {"sent": 3, "ok": 1, "failed": 1, "errored": 0, "unanswered": 1}
    assert commands["by_kind"]["accept"] == {"sent": 1, "ok": 0, "failed": 0, "errored": 1, "unanswered": 0}
    assert commands["failures"] == {"NOT_OPEN": 1, "REQUEST_CAPACITY_EXCEEDED": 1}


def test_ready_is_not_counted_as_a_command(tmp_path):
    commands = summary_of(finished_run(tmp_path))["commands"]
    assert "ready" not in commands["by_kind"]


def test_a_retried_command_counts_once_and_is_noted(tmp_path):
    log = Log(tmp_path)
    log.state(0)
    log.offer("req-1")
    log.offer("req-1")
    log.result("req-1")
    commands = summary_of(tmp_path)["commands"]
    assert commands["sent"] == 1
    assert commands["retried"] == 1
    assert commands["by_kind"]["offer"]["ok"] == 1


def test_a_protocol_error_that_names_no_command_is_counted_apart(tmp_path):
    log = Log(tmp_path)
    log.state(0)
    log.protocol_error(None, model.ControlCode.CONTROL_CODE_SESSION_FENCED)
    commands = summary_of(tmp_path)["commands"]
    assert commands["unmatched_protocol_errors"] == {"SESSION_FENCED": 1}
    assert commands["sent"] == 0


# --- the curves -------------------------------------------------------------

def test_every_series_shares_one_tick_axis(tmp_path):
    series = summary_of(finished_run(tmp_path))["series"]
    assert series["tick"] == [0, 1, 2]
    lengths = {len(v) for k, v in series.items() if isinstance(v, list)}
    lengths |= {len(v) for k, d in series.items() if isinstance(d, dict) for v in d.values()}
    assert lengths == {3}


def test_the_last_state_seen_in_a_tick_is_the_one_charted(tmp_path):
    series = summary_of(finished_run(tmp_path))["series"]
    assert series["health"] == [100, 90, 70]
    assert series["inventory"]["water"] == [30, 29, 2]
    assert series["unmet_upkeep"]["food"] == [0, 0, 1]


def test_trades_and_commands_are_bucketed_by_the_tick_they_happened_in(tmp_path):
    series = summary_of(finished_run(tmp_path))["series"]
    assert series["trades"] == [0, 1, 1]
    # req-1..3 went out while tick 0 was current, req-4 during tick 1.
    assert series["commands"] == [3, 1, 0]
    assert series["commands_failed"] == [2, 0, 0]


# --- odd logs ---------------------------------------------------------------

def test_each_run_id_in_one_log_gets_its_own_summary(tmp_path):
    Log(tmp_path, "run-1").state(3)
    Log(tmp_path, "run-2").state(7)
    runs = json.loads(write_summary(tmp_path).read_text())["runs"]
    assert [(r["run_id"], r["result"]["last_tick"]) for r in runs] == [("run-1", 3), ("run-2", 7)]


def test_commands_sent_before_any_state_still_summarise(tmp_path):
    Log(tmp_path).offer("req-1")
    run = summary_of(tmp_path)
    assert run["commands"]["sent"] == 1
    assert run["series"]["tick"] == []
    assert run["result"]["progress"] is None


def test_rows_from_an_older_client_with_fields_missing_do_not_raise():
    rows = [{"direction": "received", "kind": "state", "message": {"run_id": "r", "tick": 4, "observation": {}}}]
    [run] = summarize_session(rows)["runs"]
    assert run["series"]["health"] == [None]
    assert run["series"]["inventory"]["water"] == [0]
    assert run["trades"]["count"] == 0


def test_context_is_carried_through_untouched(tmp_path):
    Log(tmp_path).state(0)
    summary = json.loads(write_summary(tmp_path, context={"policy": "hustler", "shadow": None}).read_text())
    assert summary["context"] == {"policy": "hustler", "shadow": None}
    assert summary["session"] == tmp_path.name


# --- files ------------------------------------------------------------------

def test_a_half_written_last_line_is_skipped_and_reported(tmp_path):
    log = Log(tmp_path)
    log.state(0)
    log.state(1)
    with (tmp_path / "messages.jsonl").open("a") as handle:
        handle.write('{"direction": "received", "kind": "sta')  # Ctrl+C mid-write
    summary = json.loads(write_summary(tmp_path).read_text())
    assert summary["log"]["skipped_lines"] == 1
    assert summary["runs"][0]["result"]["last_tick"] == 1


def test_a_run_that_never_logged_a_message_still_leaves_a_summary(tmp_path):
    path = write_summary(tmp_path)
    summary = json.loads(path.read_text())
    assert summary["runs"] == []
    assert summary["schema_version"] == 1


def test_writing_a_summary_leaves_no_scratch_file_behind(tmp_path):
    Log(tmp_path).state(0)
    write_summary(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [SUMMARY_FILE, "messages.jsonl"]


def test_a_summary_is_rewritten_not_appended_to(tmp_path):
    Log(tmp_path).state(0)
    write_summary(tmp_path)
    Log(tmp_path).state(5)
    assert summary_of(tmp_path)["result"]["last_tick"] == 5


def test_read_rows_ignores_blank_lines_and_non_objects(tmp_path):
    path = tmp_path / "messages.jsonl"
    path.write_text('\n{"a": 1}\n[1, 2]\nnot json\n')
    assert read_rows(path) == ([{"a": 1}], 2)


def test_run_dirs_finds_a_single_run_or_a_directory_of_them(tmp_path):
    for name in ("b", "a"):
        Log(tmp_path / name).state(0)
    (tmp_path / "empty").mkdir()
    assert [d.name for d in run_dirs(tmp_path)] == ["a", "b"]
    assert run_dirs(tmp_path / "a") == [tmp_path / "a"]


def test_the_command_line_summarises_every_run_under_a_directory(tmp_path, capsys):
    for name in ("a", "b"):
        Log(tmp_path / name).state(0)
    assert history_main([str(tmp_path)]) == 0
    assert (tmp_path / "a" / SUMMARY_FILE).exists() and (tmp_path / "b" / SUMMARY_FILE).exists()
    assert "2 summarised, 0 failed" in capsys.readouterr().out


def test_the_command_line_fails_on_a_path_that_is_not_a_directory(tmp_path, capsys):
    assert history_main([str(tmp_path / "nope")]) == 1


# --- the hook in the client -------------------------------------------------

def test_the_clients_end_of_run_hook_writes_the_summary_with_the_policy(tmp_path, capsys):
    Log(tmp_path).state(0)
    cli.write_history(tmp_path, argparse.Namespace(policy="scrooge", shadow="hustler"))
    written = json.loads((tmp_path / SUMMARY_FILE).read_text())
    assert written["context"] == {"policy": "scrooge", "shadow": "hustler"}


def test_the_clients_end_of_run_hook_never_raises(tmp_path, capsys):
    cli.write_history(tmp_path / "missing" / "dir", argparse.Namespace(policy="scrooge", shadow=None))
    assert "history: not written" in capsys.readouterr().out
