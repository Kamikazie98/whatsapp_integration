
import sys
from unittest.mock import MagicMock

# Mock frappe module
class FrappeMock(MagicMock):
    def __getattr__(self, name):
        if name == "get_doc":
            return MagicMock()
        if name == "enqueue":
            return MagicMock()
        if name == "logger":
            return MagicMock(return_value=MagicMock())
        return MagicMock()

sys.modules["frappe"] = FrappeMock()
sys.modules["frappe.model.document"] = MagicMock()
sys.modules["frappe.tests.utils"] = MagicMock()
