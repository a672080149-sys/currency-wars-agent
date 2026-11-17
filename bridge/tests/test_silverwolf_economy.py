"""Offline checks for economy decisions; no game input or model calls."""
import ast
import contextlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch

os.environ["CW_TRAILBLAZER_NAME"] = "CT"  # 测试用的开拓者昵称（真实使用时在 player.json 里填自己的）

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))
import duo


def slot(name, stars=1, zone="front"):
    return {"zone": zone, "occupied": True, "blocked": False, "unit": {"name": name, "stars": stars}}


def board(level=7, gold=70, stars=2):
    return {"level": level, "gold": gold, "slots": [slot("银狼LV.999", stars), slot("真珠", 2)]}


class EconomyTests(unittest.TestCase):
    def setUp(self):
        self.lineup = patch.object(duo, "current_lineup", return_value="silverwolf")
        self.lineup.start()
        self.addCleanup(self.lineup.stop)

    def action(self, state, action, results=None):
        with patch.object(duo, "read_lineup_board", return_value=state), patch.object(duo, "call") as call:
            call.side_effect = results or [{"bought": 0, "rolls": 0}]
            result = duo.run_action(action)
            return result, call.call_args_list

    def test_level_before_seven_spends_to_fifty(self):
        result, calls = self.action(board(level=6), {"do": "exp", "floor": 0, "times": 2, "until_level": 10},
                                    [{"bought": 5, "level": 7, "gold": 50}])
        self.assertEqual(calls[0].args, ("exp", {"times": 0, "until_level": 7, "floor": 50}))
        self.assertTrue(result["needs_review"])

    def test_no_paid_leveling_until_actual_three_star(self):
        for level in (7, 8):
            result, calls = self.action(board(level=level), {"do": "exp"})
            self.assertIn("skipped", result)
            self.assertEqual(calls, [])

    def test_roll_targets_silverwolf_and_buys_pearl_without_chasing_it(self):
        result, calls = self.action(board(), {"do": "roll", "floor": 0, "max_rolls": 2},
                                    [{"bought": {}, "rolls": 6, "gold": 58}])
        args = calls[0].args[1]
        self.assertEqual(args["floor"], 55)
        self.assertGreater(args["max_rolls"], 5)
        self.assertEqual(args["stop_targets"], ["银狼LV.999"])
        self.assertEqual(args["want"]["真珠"], 6)
        self.assertTrue(result["needs_review"])

    def test_three_star_levels_to_nine_only(self):
        _, calls = self.action(board(stars=3), {"do": "exp", "until_level": 10})
        self.assertEqual(calls[0].args[1], {"times": 0, "until_level": 9, "floor": 50})
        result, calls = self.action(board(level=9, stars=3), {"do": "exp"})
        self.assertIn("skipped", result)
        self.assertEqual(calls, [])
        state = board(level=9, stars=3)
        state["slots"].append(slot("星神☆阿哈", None))
        _, calls = self.action(state, {"do": "roll"})
        self.assertEqual(calls[0].args[1]["always_buy"], ["星神★阿哈"])
        self.assertEqual(calls[0].args[1]["max_rolls"], 0)
        state["stage"] = "3-7"
        _, calls = self.action(state, {"do": "roll"})
        self.assertEqual(calls[0].args[1]["floor"], 0)
        self.assertGreater(calls[0].args[1]["max_rolls"], 0)

    def test_fight_checks_economy_and_defers_after_change(self):
        result, calls = self.action(board(level=6), {"do": "fight"},
                                    [{"bought": {}, "rolls": 0}, {"bought": 3, "level": 7, "gold": 58}])
        self.assertEqual([c.args[0] for c in calls], ["roll", "exp"])
        self.assertEqual(calls[0].args[1]["max_rolls"], 0)
        self.assertTrue(result["needs_review"])

    def test_nine_only_buys_current_shop_then_fights(self):
        _, calls = self.action(board(level=9, stars=3), {"do": "fight"},
                               [{"bought": {}, "rolls": 0}, {"page": "BATTLE"}])
        self.assertEqual([c.args[0] for c in calls], ["roll", "fight"])
        self.assertEqual(calls[0].args[1]["max_rolls"], 0)

    def test_sparkle_retired_only_after_aventurine_is_fielded(self):
        state = board()
        self.assertIn("花火", duo.lineup_needs(state))
        state["slots"].append(slot("砂金•戏浪", zone="bench"))
        self.assertIn("花火", duo.lineup_needs(state))
        state["slots"][-1]["zone"] = "front"
        self.assertNotIn("花火", duo.lineup_needs(state))
        self.assertEqual(duo.lineup_targets(state)["真珠"], 3)

    def test_unmerged_copies_do_not_trigger_more_rolling_or_leveling(self):
        state = board()
        state["slots"] += [slot("银狼LV.999", 2, "bench"), slot("银狼LV.999", 2, "bench")]
        result, calls = self.action(state, {"do": "fight"})
        self.assertIn("error", result)
        self.assertEqual(calls, [])

    def test_board_read_failure_stops_without_guessing(self):
        with patch.object(duo, "call", side_effect=[{"page": "BOARD"}, {"error": "OCR failed"}]):
            with self.assertRaises(duo.BridgeError):
                duo.lineup_needs()

    def test_missing_config_does_not_turn_into_empty_needs(self):
        with patch.object(duo, "lineup_targets", side_effect=OSError("missing")):
            with self.assertRaises(duo.BridgeError):
                duo.lineup_needs(board())

    def test_explicit_upgrade_kept_but_retired_sparkle_not_rebought(self):
        state = board()
        state["slots"].append(slot("砂金·戏浪", 2))
        _, calls = self.action(state, {"do": "roll", "want": {"高费替换测试角色": 1, "花火": 3, "幸运星": 5}})
        want = calls[0].args[1]["want"]
        self.assertEqual(want["高费替换测试角色"], 1)
        self.assertEqual(want["幸运星"], 1)
        self.assertNotIn("花火", want)

    def test_ct_alias_counts_owned_copies_against_final_slot(self):
        state = board()
        state["slots"].append(slot("CT", 1, "back"))
        self.assertEqual(duo.lineup_needs(state)["开拓者·欢愉"], 2)
        state["slots"].append(slot("开拓者•欢愉", 1, "bench"))
        self.assertEqual(duo.lineup_needs(state)["开拓者·欢愉"], 1)
        state["slots"][-1]["unit"]["stars"] = 2
        self.assertNotIn("开拓者·欢愉", duo.lineup_needs(state))

    def test_plan_stops_before_stale_moves_and_fight(self):
        state = {"status": "agreed", "plan": [{"do": "exp"}, {"do": "move"}, {"do": "fight"}]}
        with patch.object(duo, "load", return_value=state), patch.object(duo, "save") as save, \
             patch.object(duo, "say"), patch.object(duo, "look", return_value="board"), \
             patch.object(duo, "run_action", return_value={"needs_review": True}) as action, \
             contextlib.redirect_stdout(io.StringIO()):
            duo.cmd_exec(None)
        self.assertEqual(action.call_count, 1)
        self.assertEqual(save.call_args.args[0]["status"], "review")


# Execute the real loops with fake frames, avoiding imports that initialize OCR or game input.
def action_methods():
    tree = ast.parse((BRIDGE / "actions.py").read_text(encoding="utf-8-sig"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Actions")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in ("roll", "exp")]
    env = {"Page": SimpleNamespace(BOARD="BOARD", SHOP="SHOP"), "norm": duo._norm,
           "NOTE": set(), "XP_BTN": (298, 864), "SHOP_ROW_AREA": (0, 0, 1, 1)}
    exec(compile(ast.Module(body=methods, type_ignores=[]), "actions.py", "exec"), env)
    return env


class ActionLoopTests(unittest.TestCase):
    def test_actual_shop_normalization_matches_ct_alias_only(self):
        tree = ast.parse((BRIDGE / "actions.py").read_text(encoding="utf-8-sig"))
        norm = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "norm")
        import player
        env = {"to_trailblazer": player.to_trailblazer}
        exec(compile(ast.Module(body=[norm], type_ignores=[]), "actions.py", "exec"), env)
        self.assertEqual(env["norm"]("CT"), env["norm"]("开拓者•欢愉"))
        self.assertEqual(env["norm"](" ct "), duo._norm("CT"))
        self.assertNotEqual(env["norm"]("开拓者·记忆"), env["norm"]("CT"))

    def test_refresh_spends_to_fifty_five_without_crossing_it(self):
        env = action_methods()
        f = SimpleNamespace(page="SHOP", gold=69, offers=[], shop_refresh_price=2)
        def refresh(frame):
            frame.gold -= 2
            return frame
        fake = SimpleNamespace(ensure_board=lambda *a: f, open_shop=lambda *a: f,
                               refresh_shop=refresh, b=SimpleNamespace(abort_flag=False))
        result = env["roll"](fake, {"auto": False, "floor": 55, "want": {"银狼LV.999": 6},
                                   "stop_targets": ["银狼LV.999"]})
        self.assertEqual((result["gold"], result["rolls"]), (55, 7))

    def test_repeat_aha_purchase_across_refresh(self):
        env = action_methods()
        offer = SimpleNamespace(name="星神☆阿哈", stars=None, cost=4, column=0, point=(0, 0))
        f = SimpleNamespace(page="SHOP", gold=70, offers=[offer, offer], shop_refresh_price=2)
        def tap(*args, **kwargs):
            f.gold -= 4
        def refresh(frame):
            frame.gold -= 2
            frame.offers = [offer]
            return frame
        env["tap_wait"] = tap
        fake = SimpleNamespace(ensure_board=lambda *a: f, open_shop=lambda *a: f, frame=lambda: f,
                               refresh_shop=refresh, b=SimpleNamespace(abort_flag=False))
        result = env["roll"](fake, {"auto": False, "always_buy": ["星神★阿哈"], "max_rolls": 1})
        self.assertEqual(result["bought"], {"星神★阿哈": 3})

    def test_exp_stops_at_fifty_with_actual_cost(self):
        env = action_methods()
        f = SimpleNamespace(page="BOARD", gold=62, level=6, experience=(0, 40), experience_cost=4, capacity=6)
        def tap(*args, **kwargs):
            f.gold -= 4
            f.experience = (f.experience[0] + 4, 40)
        env["tap_wait"] = tap
        fake = SimpleNamespace(ensure_board=lambda: f, frame=lambda: f, b=SimpleNamespace(abort_flag=False))
        result = env["exp"](fake, {"floor": 50, "until_level": 7})
        self.assertEqual((result["gold"], result["bought"]), (50, 3))

    def test_failed_exp_click_does_not_loop_forever(self):
        env = action_methods()
        env["tap_wait"] = lambda *a, **k: None
        f = SimpleNamespace(page="BOARD", gold=70, level=6, experience=(0, 40), experience_cost=4)
        fake = SimpleNamespace(ensure_board=lambda: f, frame=lambda: f, b=SimpleNamespace(abort_flag=False))
        result = env["exp"](fake, {"floor": 50, "until_level": 7})
        self.assertIn("error", result)
        self.assertEqual(result["bought"], 0)

    def test_core_completion_does_not_keep_rolling_for_pearl(self):
        for offer_stars, missing in ((2, 3), (3, 9)):
            env = action_methods()
            offer = SimpleNamespace(name="银狼LV.999", stars=offer_stars, cost=3, column=0, point=(0, 0))
            f = SimpleNamespace(page="SHOP", gold=70, offers=[offer], shop_refresh_price=2)
            def tap(*args, **kwargs):
                f.gold -= 3
                f.offers = []
            env["tap_wait"] = tap
            fake = SimpleNamespace(ensure_board=lambda *a: f, open_shop=lambda *a: f,
                                   frame=lambda: f, b=SimpleNamespace(abort_flag=False))
            result = env["roll"](fake, {"auto": False, "want": {"银狼LV.999": missing, "真珠": 6},
                                       "floor": 55, "stop_targets": ["银狼LV.999"]})
            self.assertEqual(result["rolls"], 0)
            self.assertEqual(result["still_want"], {"真珠": 6})

    def test_full_bench_keeps_core_purchase_failure_visible(self):
        env = action_methods()
        env["tap_wait"] = lambda *a, **k: None
        f = SimpleNamespace(page="SHOP", gold=70, shop_refresh_price=2,
                            offers=[SimpleNamespace(name="银狼LV.999", stars=1, cost=3, column=0, point=(0, 0))])
        fake = SimpleNamespace(ensure_board=lambda *a: f, open_shop=lambda *a: f,
                               frame=lambda: f, b=SimpleNamespace(abort_flag=False))
        result = env["roll"](fake, {"auto": False, "want": {"银狼LV.999": 1}, "stop_targets": ["银狼LV.999"]})
        self.assertIn("error", result)
        self.assertEqual(result["rolls"], 0)


if __name__ == "__main__":
    unittest.main()


class TrailblazerNameTests(unittest.TestCase):
    def test_nickname_comes_from_config_not_hardcoded(self):
        import player
        with patch.dict(os.environ, {"CW_TRAILBLAZER_NAME": "星际旅人"}), patch.object(player, "CONFIG", "__no_such_file__.json"):
            self.assertEqual(player.to_trailblazer("星际旅人"), "开拓者·欢愉")
            self.assertEqual(player.to_trailblazer("CT"), "CT")

    def test_no_config_means_no_mapping(self):
        import player
        with patch.dict(os.environ, {"CW_TRAILBLAZER_NAME": ""}), patch.object(player, "CONFIG", "__no_such_file__.json"):
            self.assertEqual(player.to_trailblazer("CT"), "CT")
            self.assertEqual(player.to_trailblazer("开拓者•欢愉"), "开拓者·欢愉")
