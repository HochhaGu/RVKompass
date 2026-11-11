"""Tests des Ablaufs: Haltepunkte, Pause, NEEDS_HUMAN, Reparatur, Limit, Neustart."""

from orchestrator.claude_runner import AUTH, LIMIT, OK, TRANSIENT, ClaudeResult
from orchestrator.engine import resume
from orchestrator.git_ops import SafetyError
from orchestrator.state import BEREIT, FERTIG, LAEUFT, PAUSIERT, MemoryStore

from .conftest import FakeGates, FakeGit, FakeRunner, ok_report, red_report

FULL_PHASE = ["plan", "review", "implement", "report"]


def test_p0_runs_through_and_halts_with_mail(make_engine):
    eng = make_engine()
    assert eng.run(forever=False) == PAUSIERT
    st = eng.store.load()
    assert eng.runner.steps() == FULL_PHASE
    assert eng.git.names() == [
        "prepare_worktree",
        "start_branch",
        "commit_all",
        "push",
        "open_pr",
        "merge_pr",
    ]
    assert st["phases"]["P0"]["status"] == "fertig"
    assert st["phase"] == "P1" and st["step"] == "branch"
    assert st["pause"]["kind"] == "haltepunkt"
    assert (
        len(eng.mailer.sent) == 1 and "Haltepunkt nach P0" in eng.mailer.sent[0].subject
    )
    assert st["digest"][0]["phase"] == "P0"  # Phase-fertig-Meldung für den Wochenbrief


def test_p0_never_runs_evaluation_baseline_but_p1_sets_it(make_engine):
    gates = FakeGates([ok_report(), ok_report(0.61)])
    eng = make_engine(gates=gates)
    eng.run(forever=False)
    assert (
        "baseline" not in eng.store.load()
    )  # P0 ohne Evaluation setzt keinen Vergleichsstand
    resume(eng.store, eng.s.pause_path)
    eng.run(only="P1", forever=False)
    st = eng.store.load()
    assert (
        st["baseline"]["metrics"]["hit@10"] == 0.61 and st["baseline"]["phase"] == "P1"
    )
    assert gates.calls == [None, None]  # P1 vergleicht mit dem letzten Bericht im Repo


def test_review_rejected_once_then_revised(make_engine):
    runner = FakeRunner(
        {
            "review": [
                ClaudeResult(
                    kind=OK,
                    structured={"freigabe": False, "gruende": ["Punkt 6 fehlt"]},
                ),
            ]
        }
    )
    eng = make_engine(runner=runner)
    eng.run(forever=False)
    assert runner.steps()[:5] == ["plan", "review", "revise", "review", "implement"]
    revise_prompt = runner.calls[2][1].prompt
    assert "Punkt 6 fehlt" in revise_prompt


def test_review_rejected_twice_pauses_and_resume_replans(make_engine):
    no = ClaudeResult(
        kind=OK, structured={"freigabe": False, "gruende": ["Lizenz geraten"]}
    )
    eng = make_engine(runner=FakeRunner({"review": [no, no]}))
    assert eng.run(forever=False) == PAUSIERT
    st = eng.store.load()
    assert (
        st["pause"]["resume_step"] == "plan"
        and "Lizenz geraten" in st["pause"]["question"]
    )
    assert "Eingreifen nötig" in eng.mailer.sent[0].subject
    resume(eng.store, eng.s.pause_path)
    st = eng.store.load()
    assert (
        st["status"] == LAEUFT
        and st["step"] == "plan"
        and st["counters"]["review"] == 0
    )


def test_red_gates_get_one_repair(make_engine):
    eng = make_engine(gates=FakeGates([red_report(), ok_report()]))
    eng.run(forever=False)
    steps = eng.runner.steps()
    assert steps[:4] == ["plan", "review", "implement", "repair"]
    repair_prompt = eng.runner.calls[3][1].prompt
    assert "AssertionError" in repair_prompt
    assert eng.store.load()["phases"]["P0"]["status"] == "fertig"


def test_red_after_repair_pauses_and_resume_retries_gates(make_engine):
    eng = make_engine(gates=FakeGates([red_report(), red_report()]))
    assert eng.run(forever=False) == PAUSIERT
    st = eng.store.load()
    assert st["pause"]["reason"] == "Gates auch nach dem Reparaturversuch rot"
    assert "push" not in eng.git.names()  # nichts ausgeliefert
    assert len(eng.mailer.sent) == 1
    resume(eng.store, eng.s.pause_path)
    st = eng.store.load()
    assert st["step"] == "gates" and st["counters"]["repair"] == 0


def test_needs_human_pauses_and_answer_is_passed_on(make_engine):
    runner = FakeRunner(
        {
            "implement": [
                ClaudeResult(
                    kind=OK,
                    text="Teil erledigt.\nNEEDS_HUMAN: Lizenztext EUPL bitte bereitstellen",
                ),
            ]
        }
    )
    eng = make_engine(runner=runner)
    assert eng.run(forever=False) == PAUSIERT
    st = eng.store.load()
    assert st["pause"]["question"] == "Lizenztext EUPL bitte bereitstellen"
    assert st["pause"]["resume_step"] == "implement"
    assert "Lizenztext" in eng.mailer.sent[0].text

    eng.s.answers_dir.mkdir(parents=True)
    (eng.s.answers_dir / "P0.md").write_text("Die Datei liegt unter docs/eupl.txt.")
    resume(eng.store, eng.s.pause_path)
    eng.run(forever=False)
    last_impl = [inv for step, inv in runner.calls if step == "implement"][-1]
    assert "docs/eupl.txt" in last_impl.prompt
    assert eng.store.load()["phases"]["P0"]["status"] == "fertig"


def test_usage_limit_waits_hourly_without_mail(make_engine):
    limit = ClaudeResult(kind=LIMIT, detail="usage limit")
    runner = FakeRunner({"implement": [limit, limit]})
    eng = make_engine(runner=runner)
    eng.run(forever=False)
    assert runner.steps().count("implement") == 3
    assert eng.clock.slept >= 2 * 3600
    assert [m for m in eng.mailer.subjects() if "Limit" in m or "Eingreifen" in m] == []
    assert eng.store.load()["phases"]["P0"]["status"] == "fertig"


def test_transient_errors_retry_then_pause(make_engine):
    t = ClaudeResult(kind=TRANSIENT, detail="overloaded")
    eng = make_engine(runner=FakeRunner({"plan": [t] * 10}))
    assert eng.run(forever=False) == PAUSIERT
    assert eng.runner.steps().count("plan") == eng.s.transient_max_attempts + 1
    assert eng.store.load()["pause"]["reason"] == "Claude ist dauerhaft gestört"


def test_auth_error_pauses_with_instructions(make_engine):
    eng = make_engine(
        runner=FakeRunner({"plan": [ClaudeResult(kind=AUTH, detail="401")]})
    )
    eng.run(forever=False)
    assert "setup-token" in eng.store.load()["pause"]["question"]


def test_pause_file_stops_after_current_step_without_mail(make_engine):
    eng = make_engine()
    store = eng.store
    original = eng.step_once

    def step_and_touch(state):
        original(state)
        eng.s.pause_path.parent.mkdir(parents=True, exist_ok=True)
        eng.s.pause_path.touch()

    eng.step_once = step_and_touch
    assert eng.run(forever=False) == PAUSIERT
    st = store.load()
    assert st["step"] == "plan"  # Branch-Schritt lief noch fertig, dann Halt
    assert eng.mailer.sent == []
    msg = resume(store, eng.s.pause_path)
    assert not eng.s.pause_path.exists() and "Fortgesetzt" in msg


def test_restart_continues_at_saved_step(make_engine, settings):
    ids = [p.id for p in settings.phases]
    store = MemoryStore(ids[0], ids)
    with store.update() as st:
        st["status"] = LAEUFT
        st["step"] = "gates"
        st["phases"]["P0"].update(status="läuft", plan_text="# Plan")
    eng = make_engine(store=store)
    eng.run(forever=False)
    assert eng.runner.steps() == ["report"]  # kein neuer Plan, keine neue Umsetzung
    assert eng.git.names()[0] == "commit_all"


def test_last_phase_sends_all_done(make_engine, settings):
    ids = [p.id for p in settings.phases]
    store = MemoryStore(ids[0], ids)
    with store.update() as st:
        st["phase"] = "P8"
        for pid in ids[:-1]:
            st["phases"][pid]["status"] = "fertig"
    eng = make_engine(store=store)
    assert eng.run(forever=False) == FERTIG
    assert any("Alles fertig" in s for s in eng.mailer.subjects())
    assert "Live-Schaltung" in eng.mailer.sent[-1].text


def test_only_runs_one_phase_and_stops(make_engine, settings):
    ids = [p.id for p in settings.phases]
    store = MemoryStore(ids[0], ids)
    with store.update() as st:
        st["phase"] = "P2"
        st["phases"]["P0"]["status"] = st["phases"]["P1"]["status"] = "fertig"
    eng = make_engine(store=store)
    assert eng.run(only="P2", forever=False) == BEREIT
    st = eng.store.load()
    assert st["phases"]["P2"]["status"] == "fertig" and st["phase"] == "P3"
    assert eng.runner.steps() == FULL_PHASE


def test_safety_error_pauses_before_push(make_engine):
    git = FakeGit(
        raise_on={"commit_all": SafetyError("Verbotene Dateien im Commit: data/x")}
    )
    eng = make_engine(git=git)
    assert eng.run(forever=False) == PAUSIERT
    assert "push" not in git.names()
    assert "Schutzprüfung" in eng.store.load()["pause"]["reason"]


def test_unexpected_exception_pauses_and_redacts(make_engine):
    git = FakeGit(
        raise_on={"start_branch": RuntimeError("Fehler mit sk-ant-geheim-123456")}
    )
    eng = make_engine(git=git)
    eng.run(forever=False)
    st = eng.store.load()
    assert st["pause"]["reason"] == "Technischer Fehler im Steuerprogramm"
    assert "sk-ant-geheim" not in st["pause"]["question"]


def test_auth_expiry_warning_sent_once(make_engine):
    soon = "2026-11-13T00:00:00Z"
    runner = FakeRunner(
        {
            "plan": [ClaudeResult(kind=OK, text="x" * 300, auth_expires_at=soon)],
            "review": [
                ClaudeResult(
                    kind=OK,
                    structured={"freigabe": True, "gruende": []},
                    auth_expires_at=soon,
                )
            ],
        }
    )
    eng = make_engine(runner=runner)
    eng.run(forever=False)
    assert sum("Anmeldung" in s for s in eng.mailer.subjects()) == 1


def test_worker_permissions_never_allow_push_or_env(make_engine):
    eng = make_engine()
    eng.run(forever=False)
    impl = next(inv for step, inv in eng.runner.calls if step == "implement")
    args = impl.args
    allow = args[args.index("--allowedTools") + 1 : args.index("--disallowedTools")]
    deny = args[args.index("--disallowedTools") + 1 :]
    assert not any("push" in a or ".env" in a or a.startswith("Bash(rm") for a in allow)
    assert {"Bash(git push *)", "Read(**/.env)", "Bash(rm *)"} <= set(deny)
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    plan = eng.runner.calls[0][1].args
    assert plan[plan.index("--permission-mode") + 1] == "plan"


def test_digest_sends_each_finished_phase_only_once():
    from orchestrator.engine import digest_key, pending_digest

    entries = [
        {"phase": "P0", "finished_at": "t0"},
        {"phase": "P1", "finished_at": "t1"},
    ]
    assert pending_digest(entries, set()) == entries
    assert pending_digest(entries, {digest_key(entries[0])}) == [entries[1]]
