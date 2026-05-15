"""
Conversation Store
------------------
Stores and retrieves farmer conversation history for the chat agent.
Each turn carries an optional `topic` (planting / pests / inputs / repayment /
weather / market / general) and `language` (en / bem / nya) so the chat agent
can carry context across days.
"""

from collections import Counter

from memory.farmer_store import get_conn


KNOWN_TOPICS = {
    "planting", "pests", "diseases", "inputs", "fertilizer", "inoculant",
    "weather", "rainfall", "drought", "harvest", "buyback", "repayment",
    "market", "price", "loan", "training", "general",
}


class ConversationStore:
    def add(
        self,
        farmer_id: str,
        role: str,
        message: str,
        *,
        topic: str | None = None,
        language: str | None = None,
    ):
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO conversations (farmer_id, role, message, topic, language)
                   VALUES (?, ?, ?, ?, ?)""",
                (farmer_id, role, message, topic, language),
            )

    def get_recent(self, farmer_id: str, n: int = 5) -> list[dict]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT role, message, topic, language, sent_at FROM conversations
                   WHERE farmer_id = ?
                   ORDER BY sent_at DESC LIMIT ?""",
                (farmer_id, n),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def get_topic_history(self, farmer_id: str, *, lookback: int = 20) -> dict:
        """Return topic-frequency stats over the last N farmer turns plus the most
        recent farmer-side topic. Useful for the chat agent's continuity prompts
        ('you were asking about pests last week — any update?')."""
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT topic, sent_at FROM conversations
                   WHERE farmer_id = ? AND role = 'farmer' AND topic IS NOT NULL
                   ORDER BY sent_at DESC LIMIT ?""",
                (farmer_id, lookback),
            ).fetchall()
        if not rows:
            return {"recent_topic": None, "topic_counts": {}, "n_turns": 0}
        topics = [r["topic"] for r in rows]
        return {
            "recent_topic": topics[0],
            "topic_counts": dict(Counter(topics)),
            "n_turns": len(topics),
        }

    def get_topic_thread(self, farmer_id: str, topic: str, *, limit: int = 10) -> list[dict]:
        """Most recent turns on a given topic — used by the chat agent to build
        context when it sees a continuation question on the same topic."""
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT role, message, language, sent_at FROM conversations
                   WHERE farmer_id = ? AND topic = ?
                   ORDER BY sent_at DESC LIMIT ?""",
                (farmer_id, topic, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]
