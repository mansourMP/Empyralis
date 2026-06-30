import unittest

from server_modules.acp_manager import _PersistentStateDict, _PersistentStateList


class AcpManagerStateReloadTests(unittest.TestCase):
    def test_state_dict_reload_treats_none_as_empty(self) -> None:
        state = _PersistentStateDict()
        state["alpha"] = "beta"

        state.reload(None)

        self.assertEqual(dict(state), {})

    def test_state_list_reload_treats_none_as_empty(self) -> None:
        state = _PersistentStateList()
        state.append("alpha")

        state.reload(None)

        self.assertEqual(list(state), [])


if __name__ == "__main__":
    unittest.main()
