"""Tests für tdf/alarms.py — Ereignis-Erkennung."""
from __future__ import annotations

import time
from dataclasses import dataclass

import pytest

from tdf import alarms as al


# --------------------------------------------------------------------------- #
# Helfer: kleine Doubles, die sich wie GroupInfo verhalten
# --------------------------------------------------------------------------- #
@dataclass
class FakeGroup:
    order: int
    gap_seconds: float
    name: str = "Gruppe"
    size: int = 5


@dataclass
class FakeGCEntry:
    bib: int
    position: int = 1


def make_evaluator():
    """Neue AlarmSystem-Instanz ohne Cooldown-Hindernisse."""
    return al.AlarmSystem()


# --------------------------------------------------------------------------- #
# Rennende
# --------------------------------------------------------------------------- #
class TestRaceEnded:
    def test_race_starts_then_ends(self):
        s = make_evaluator()
        # Erster Tick: Rennen läuft
        s.evaluate({}, [], [], set(), race_status=True)
        # Zweiter Tick: Rennen beendet
        new = s.evaluate({}, [], [], set(), race_status=False)
        kinds = [a.kind for a in new]
        assert "race_ended" in kinds

    def test_no_alarm_if_never_started(self):
        # Wenn prev_race_status None ist (vor Start), kein Alarm
        s = make_evaluator()
        new = s.evaluate({}, [], [], set(), race_status=False)
        assert all(a.kind != "race_ended" for a in new)

    def test_no_alarm_if_still_running(self):
        s = make_evaluator()
        s.evaluate({}, [], [], set(), race_status=True)
        new = s.evaluate({}, [], [], set(), race_status=True)
        assert all(a.kind != "race_ended" for a in new)


# --------------------------------------------------------------------------- #
# Ausreißer / Eingeholt
# --------------------------------------------------------------------------- #
class TestBreakaway:
    def test_breakaway_detected(self):
        s = make_evaluator()
        # Gruppe mit kleinem Gap
        s.evaluate({}, [FakeGroup(order=1, gap_seconds=5.0, name="Ausreißer")],
                   [], set(), race_status=True)
        # Plötzlich großer Gap (>30s, vorher <15s)
        new = s.evaluate({}, [FakeGroup(order=1, gap_seconds=60.0, name="Ausreißer")],
                         [], set(), race_status=True)
        kinds = [a.kind for a in new]
        assert "breakaway" in kinds
        # Nachricht enthält den Gruppennamen
        bw = next(a for a in new if a.kind == "breakaway")
        assert "Ausreißer" in bw.message

    def test_breakaway_no_alarm_without_history(self):
        # Beim ersten Tick kann es keinen Ausreißer geben (kein prev_gap)
        s = make_evaluator()
        new = s.evaluate({}, [FakeGroup(order=1, gap_seconds=60.0)], [],
                         set(), race_status=True)
        assert all(a.kind != "breakaway" for a in new)

    def test_breakaway_no_alarm_when_gradual(self):
        # Gap wächst langsam: 5 -> 20 -> 40 — bei 40 war prev schon >15
        s = make_evaluator()
        s.evaluate({}, [FakeGroup(order=1, gap_seconds=5.0)], [], set(), True)
        s.evaluate({}, [FakeGroup(order=1, gap_seconds=20.0)], [], set(), True)
        new = s.evaluate({}, [FakeGroup(order=1, gap_seconds=40.0)], [], set(), True)
        # 40 >= 30, aber prev=20 > 30*0.5=15 -> kein Ausreißer-Alarm
        assert all(a.kind != "breakaway" for a in new)


class TestCaught:
    def test_caught_detected(self):
        s = make_evaluator()
        # Großer Gap
        s.evaluate({}, [FakeGroup(order=1, gap_seconds=60.0, name="Ausreißer")],
                   [], set(), True)
        # Eingeholt auf kleinen Gap (<5s, vorher >10)
        new = s.evaluate({}, [FakeGroup(order=1, gap_seconds=2.0, name="Ausreißer")],
                         [], set(), True)
        kinds = [a.kind for a in new]
        assert "caught" in kinds


# --------------------------------------------------------------------------- #
# Trikotwechsel
# --------------------------------------------------------------------------- #
class TestJerseyChange:
    def test_jersey_change_detected(self):
        s = make_evaluator()
        # Vingegaard (11) trägt Gelb
        s.evaluate({"Y": 11}, [], [], set(), True)
        # Pogacar (1) übernimmt Gelb
        new = s.evaluate({"Y": 1}, [], [], set(), True)
        kinds = [a.kind for a in new]
        assert "jersey_change" in kinds
        jc = next(a for a in new if a.kind == "jersey_change")
        assert jc.details["old_bib"] == 11
        assert jc.details["new_bib"] == 1
        assert jc.details["jersey_code"] == "Y"
        assert "Gelb" in jc.message

    def test_all_four_jerseys(self):
        # Testet alle 4 Trikot-Farben durch
        for code, name in [("Y", "Gelb"), ("G", "Grün"), ("P", "Berg"), ("W", "Weiß")]:
            s = make_evaluator()
            s.evaluate({code: 11}, [], [], set(), True)
            new = s.evaluate({code: 22}, [], [], set(), True)
            assert any(a.kind == "jersey_change" and a.details["jersey_code"] == code
                       for a in new), f"{name}-Wechsel nicht erkannt"

    def test_no_alarm_when_same_jersey(self):
        s = make_evaluator()
        s.evaluate({"Y": 11}, [], [], set(), True)
        new = s.evaluate({"Y": 11}, [], [], set(), True)
        assert all(a.kind != "jersey_change" for a in new)


# --------------------------------------------------------------------------- #
# GC-Führungswechsel
# --------------------------------------------------------------------------- #
class TestGCLeaderChange:
    def test_leader_change_detected(self):
        s = make_evaluator()
        gc1 = [FakeGCEntry(bib=11, position=1), FakeGCEntry(bib=1, position=2)]
        s.evaluate({}, [], gc1, set(), True)
        gc2 = [FakeGCEntry(bib=1, position=1), FakeGCEntry(bib=11, position=2)]
        new = s.evaluate({}, [], gc2, set(), True)
        kinds = [a.kind for a in new]
        assert "gc_leader_change" in kinds

    def test_no_alarm_on_first_tick(self):
        s = make_evaluator()
        gc = [FakeGCEntry(bib=11, position=1)]
        new = s.evaluate({}, [], gc, set(), True)
        assert all(a.kind != "gc_leader_change" for a in new)

    def test_no_alarm_when_leader_stays(self):
        s = make_evaluator()
        gc = [FakeGCEntry(bib=11, position=1)]
        s.evaluate({}, [], gc, set(), True)
        new = s.evaluate({}, [], gc, set(), True)
        assert all(a.kind != "gc_leader_change" for a in new)

    def test_works_with_dict_entries(self):
        # Module soll auch Dicts akzeptieren
        s = make_evaluator()
        s.evaluate({}, [], [{"bib": 11}], set(), True)
        new = s.evaluate({}, [], [{"bib": 1}], set(), True)
        assert any(a.kind == "gc_leader_change" for a in new)


# --------------------------------------------------------------------------- #
# Aufgaben
# --------------------------------------------------------------------------- #
class TestWithdrawals:
    def test_new_withdrawal_detected(self):
        s = make_evaluator()
        s.evaluate({}, [], [], set(), True)
        new = s.evaluate({}, [], [], {11}, True)
        kinds = [a.kind for a in new]
        assert "withdrawal" in kinds
        wd = next(a for a in new if a.kind == "withdrawal")
        assert wd.details["bib"] == 11

    def test_multiple_new_withdrawals_all_emit(self):
        # REGRESSION-Test für den früheren Cooldown-Bug: früher teilten
        # sich alle Withdrawals den Tag "withdrawal", sodass nur die erste
        # durchkam. Mit dem Pro-Bib-_tag ("withdrawal:11", "withdrawal:22"...)
        # feuert jetzt jede eigene Aufgabe ihren Alarm.
        s = make_evaluator()
        s.evaluate({}, [], [], set(), True)
        new = s.evaluate({}, [], [], {11, 22, 33}, True)
        wds = [a for a in new if a.kind == "withdrawal"]
        assert len(wds) == 3
        bibs = {a.details["bib"] for a in wds}
        assert bibs == {11, 22, 33}

    def test_same_withdrawal_blocked_by_cooldown(self):
        # Dieselbe Bib darf nicht sofort nochmal feuern (Cooldown pro Bib).
        s = make_evaluator()
        s.evaluate({}, [], [], set(), True)
        s.evaluate({}, [], [], {11}, True)
        # Zweiter Tick mit derselben Bib (kein Delta) -> kein neuer Alarm
        new = s.evaluate({}, [], [], {11}, True)
        assert all(a.kind != "withdrawal" for a in new)

    def test_different_bibs_both_emit_same_call(self):
        # Zwei verschiedene Bibs in einem Call -> beide Alarme
        s = make_evaluator()
        s.evaluate({}, [], [], set(), True)
        new = s.evaluate({}, [], [], {44, 55}, True)
        wds = [a for a in new if a.kind == "withdrawal"]
        assert len(wds) == 2

    def test_withdrawal_after_cooldown_window(self):
        # Nach Ablauf des Cooldowns (15s) kommt ein neuer Withdrawal durch
        s = make_evaluator()
        s.evaluate({}, [], [], set(), True)
        s.evaluate({}, [], [], {11}, True)
        # Cooldown künstlich zurücksetzen (simuliert 16s später)
        s._last_seen["withdrawal:11"] -= 16.0
        # Bib 11 erneut (z.B. "wieder drin" -> "wieder raus") -> durch
        s._prev_withdrawals = set()
        new = s.evaluate({}, [], [], {11}, True)
        wds = [a for a in new if a.kind == "withdrawal"]
        assert len(wds) == 1

    def test_no_alarm_when_no_new(self):
        s = make_evaluator()
        s.evaluate({}, [], [], {11}, True)
        new = s.evaluate({}, [], [], {11}, True)
        assert all(a.kind != "withdrawal" for a in new)


# --------------------------------------------------------------------------- #
# Cooldown / Ringpuffer
# --------------------------------------------------------------------------- #
class TestAlarmBuffer:
    def test_max_alarms_enforced(self):
        # Generiere > MAX_ALARMS Alarme durch Tasks (verschiedene Bibs -> verschiedene Tags)
        s = make_evaluator()
        # Erster Tick mit Startaufgebot
        for i in range(al.MAX_ALARMS + 20):
            s._prev_withdrawals = set()
            s.evaluate({}, [], [], {1000 + i}, True)
        assert len(s._alarms) <= al.MAX_ALARMS

    def test_get_alarms_returns_serializable(self):
        s = make_evaluator()
        s.evaluate({}, [], [], set(), True)
        s.evaluate({}, [], [], {11}, True)
        result = s.get_alarms(limit=10)
        assert isinstance(result, list)
        assert all(isinstance(r, dict) for r in result)
        # Reihenfolge: neueste zuerst
        assert result[0]["kind"] == "withdrawal"

    def test_get_alarms_limit(self):
        # Erzeuge mehrere VERSCHIEDENE Alarmtypen (Trikotwechsel über
        # verschiedene Codes), da withdrawals denselben Cooldown teilen.
        s = make_evaluator()
        codes = ["Y", "G", "P", "W"]
        bibs = [11, 22, 33, 44, 55, 66, 77, 88]
        for i in range(8):
            s._prev_jerseys = {codes[i % 4]: bibs[(i - 1) % 8]} if i > 0 else {}
            s.evaluate({codes[i % 4]: bibs[i]}, [], [], set(), True)
        result = s.get_alarms(limit=5)
        assert len(result) <= 5

    def test_get_alarms_limit_zero_returns_all(self):
        # limit=0 -> alle Alarme
        s = make_evaluator()
        s.evaluate({"Y": 11}, [], [], set(), True)
        s._prev_jerseys = {"Y": 11}
        s.evaluate({"Y": 22}, [], [], set(), True)
        result = s.get_alarms(limit=0)
        assert len(result) >= 1
