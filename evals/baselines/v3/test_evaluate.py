import copy
import unittest
from evaluate import assess

class ProposalEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.sources = [{"id": "s", "text": "Если номер тихий и окна во двор, согласен.\nReceived: 2026-10-04T09:30:00+03:00\nОтвет клиента принят."}]
        self.case = {"questions": ["alternative_acceptance"], "sources": [], "catalog": []}
        self.pack = {"case_id": "check", "facts": [{"key": "alternative_acceptance", "value": "conditional",
                      "evidence": [{"source_id": "s", "fragments": ["Если номер тихий и окна во двор, согласен."]}]}],
                     "timeline": [], "readiness": "request_evidence",
                     "follow_up": {"action": "ask_user", "locator": None, "requirements": ["quiet_room", "courtyard_windows"]}}
        self.ref = {"facts": {"alternative_acceptance": {"value": "conditional", "evidence_sets": [[{"source_id": "s", "anchor": "Если номер тихий и окна во двор, согласен."}]]}},
                    "timeline": [], "optional_timeline": [], "readiness": "request_evidence",
                    "follow_up": {"action": "ask_user", "locator": None, "requirements": ["quiet_room", "courtyard_windows"]}}

    def test_supported_complete_request_passes(self):
        self.assertTrue(assess(self.pack, self.ref, self.case, self.sources)["pack_pass"])

    def test_nonempty_request_can_still_miss_a_condition(self):
        partial = copy.deepcopy(self.pack)
        partial["follow_up"]["requirements"] = ["courtyard_windows"]
        self.assertFalse(assess(partial, self.ref, self.case, self.sources)["followup_complete"])

    def test_optional_event_needs_full_support_too(self):
        optional = {"event_type": "customer_message_received", "at": "2026-10-04T09:30:00+03:00", "role": "received",
                    "evidence_sets": [[{"source_id": "s", "anchor": "Received: 2026-10-04T09:30:00+03:00"}, {"source_id": "s", "anchor": "Ответ клиента принят."}]]}
        self.ref["optional_timeline"] = [optional]
        self.pack["timeline"] = [{"event_type": "customer_message_received", "time": {"value": optional["at"], "role": "received"},
                                  "evidence": [{"source_id": "s", "fragments": ["Received: 2026-10-04T09:30:00+03:00"]}]}]
        self.assertFalse(assess(self.pack, self.ref, self.case, self.sources)["timeline_complete"])
        self.pack["timeline"][0]["evidence"][0]["fragments"].append("Ответ клиента принят.")
        self.assertTrue(assess(self.pack, self.ref, self.case, self.sources)["pack_pass"])

    def test_correct_citation_does_not_rescue_wrong_fact(self):
        wrong = copy.deepcopy(self.pack)
        wrong["facts"][0]["value"] = "confirmed"
        self.assertFalse(assess(wrong, self.ref, self.case, self.sources)["pack_pass"])

if __name__ == "__main__":
    unittest.main()
