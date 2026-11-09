"""Regression counterexamples: real slices/roles/requirements, no model mocks."""
import unittest
from contract import resolve_evidence, covers, time_errors, followup_errors, render_requirements, money_display

class EvidenceContractTests(unittest.TestCase):
    def setUp(self):
        self.sources = [{"id": "s1", "text": "Date: 2026-10-04T09:25:00+03:00\nReceived: 2026-10-04T09:30:00+03:00\nThread: 7\nПодтверждаю перенос. Этот вариант подходит."}]

    def test_disjoint_literal_fragments_keep_separate_real_spans(self):
        evidence = [{"source_id": "s1", "fragments": ["Date: 2026-10-04T09:25:00+03:00", "Подтверждаю перенос."]}]
        result = resolve_evidence(evidence, self.sources)
        self.assertTrue(result["valid"])
        self.assertEqual(len(result["spans"]), 2)
        for span in result["spans"]:
            self.assertEqual(self.sources[0]["text"][span["start"]:span["end"]], span["fragment"])

    def test_glued_nonadjacent_text_is_not_a_quote(self):
        self.assertFalse(resolve_evidence([{"source_id": "s1", "fragments": ["Date: 2026-10-04T09:25:00+03:00 Подтверждаю перенос."]}], self.sources)["valid"])

    def test_two_literal_fragments_can_cover_one_anchor(self):
        items = [{"source_id": "s1", "fragments": ["Подтверждаю перенос.", "Этот вариант подходит."]}]
        self.assertTrue(covers(items, [[{"source_id": "s1", "anchor": "Подтверждаю перенос. Этот вариант подходит."}]], self.sources))

    def test_deleted_negation_cannot_be_filled_by_concatenation(self):
        sources = [{"id": "s1", "text": "Я не согласен."}]
        items = [{"source_id": "s1", "fragments": ["Я", "согласен."]}]
        self.assertFalse(covers(items, [[{"source_id": "s1", "anchor": "Я не согласен."}]], sources))

    def test_support_never_leaks_between_source_ids(self):
        sources = [{"id": "s1", "text": "Подтверждаю."}, {"id": "s2", "text": "Подтверждаю."}]
        self.assertFalse(covers([{"source_id": "s1", "fragments": ["Подтверждаю."]}], [[{"source_id": "s2", "anchor": "Подтверждаю."}]], sources))

    def test_equivalent_source_option_is_accepted(self):
        sources = [{"id": "receipt", "text": "Списание завершено."}, {"id": "ledger", "text": "capture ok"}]
        sets = [[{"source_id": "ledger", "anchor": "capture ok"}], [{"source_id": "receipt", "anchor": "Списание завершено."}]]
        self.assertTrue(covers([{"source_id": "receipt", "fragments": ["Списание завершено."]}], sets, sources))

    def test_received_cannot_masquerade_as_sent_reply(self):
        event = {"event_type": "customer_replied", "time": {"value": "2026-10-04T09:30:00+03:00", "role": "received"},
                 "evidence": [{"source_id": "s1", "fragments": ["Received: 2026-10-04T09:30:00+03:00"]}]}
        self.assertIn("wrong_time_role", time_errors(event, self.sources))

    def test_event_time_requires_visible_literal_support(self):
        event = {"event_type": "customer_replied", "time": {"value": "2026-10-04T08:25:00+03:00", "role": "sent"},
                 "evidence": [{"source_id": "s1", "fragments": ["Date: 2026-10-04T09:25:00+03:00"]}]}
        self.assertIn("time_not_in_fragments", time_errors(event, self.sources))

    def test_bank_document_cannot_route_to_payment_query(self):
        case = {"sources": [{"locator": "doc-9", "kind": "document"}], "catalog": []}
        sources = [{"id": "ledger", "text": "Банковский документ: doc-9"}]
        action = {"action": "query_payment", "locator": "doc-9", "requirements": ["bank_settlement_document"]}
        self.assertIn("wrong_lookup_kind", followup_errors(action, case, sources))

    def test_unseen_locator_is_not_authorized_by_existence(self):
        case = {"sources": [{"locator": "hidden", "kind": "document"}], "catalog": []}
        action = {"action": "read_source", "locator": "hidden", "requirements": ["original_customer_message"]}
        self.assertIn("locator_not_visible", followup_errors(action, case, []))

    def test_each_selected_condition_gets_an_operator_question(self):
        questions = render_requirements(["quiet_room", "courtyard_windows"])
        self.assertEqual({q["key"] for q in questions}, {"quiet_room", "courtyard_windows"})
        self.assertTrue(any("тих" in q["text"] for q in questions))
        self.assertTrue(any("двор" in q["text"] for q in questions))

    def test_minor_units_are_formatted_by_code(self):
        self.assertEqual(money_display({"amount_minor": 12000, "currency": "USD", "scale": 2}), "120.00 USD")

if __name__ == "__main__":
    unittest.main()
