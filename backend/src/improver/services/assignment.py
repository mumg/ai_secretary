from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from email.utils import getaddresses

from improver.config import IdentityConfig
from improver.models import CommunicationEvent


@dataclass(frozen=True)
class AssignmentSignals:
    directly_mentioned: bool
    user_is_recipient: bool
    sole_recipient: bool
    addressed_by_name: bool

    @property
    def eligible(self) -> bool:
        return self.directly_mentioned or self.sole_recipient or (
            self.user_is_recipient and self.addressed_by_name
        )

    def model_dump(self) -> dict[str, bool]:
        return {**asdict(self), "eligible": self.eligible}


def _email_addresses(*values: str) -> set[str]:
    return {address.casefold() for _, address in getaddresses(values) if "@" in address}


def _name_tokens(identity: IdentityConfig) -> set[str]:
    tokens: set[str] = set()
    for configured_name in identity.names:
        parts = re.findall(r"[\w-]+", configured_name.casefold(), flags=re.UNICODE)
        tokens.update(part for part in parts if len(part) >= 2)
    return tokens


def _mention_aliases(identity: IdentityConfig) -> set[str]:
    aliases = _name_tokens(identity)
    for address in identity.addresses:
        local_part = address.split("@", 1)[0].casefold()
        if local_part:
            aliases.add(local_part)
    return aliases


def _participant_is_user(
    participant: dict[str, object],
    user_addresses: set[str],
    user_name_tokens: set[str],
) -> bool:
    participant_addresses = _email_addresses(
        str(participant.get("address") or ""),
        str(participant.get("email") or ""),
    )
    if participant_addresses & user_addresses:
        return True
    participant_name = str(participant.get("name") or "").casefold()
    return bool(
        participant_name
        and any(
            re.search(rf"(?<!\w){re.escape(token)}(?!\w)", participant_name)
            for token in user_name_tokens
        )
    )


def assignment_signals(
    event: CommunicationEvent,
    identity: IdentityConfig,
) -> AssignmentSignals:
    body = event.body or ""
    normalized_body = body.casefold()
    mentions = set(re.findall(r"(?<!\w)@([\w.-]+)", normalized_body, flags=re.UNICODE))
    directly_mentioned = bool(mentions & _mention_aliases(identity))

    user_addresses = {address.casefold() for address in identity.addresses}
    user_name_tokens = _name_tokens(identity)
    author_addresses = _email_addresses(event.author or "")
    recipients: list[dict[str, object]] = []
    for raw_participant in event.participants:
        if not isinstance(raw_participant, dict):
            continue
        participant = raw_participant
        role = str(participant.get("role") or "").casefold()
        participant_addresses = _email_addresses(
            str(participant.get("address") or ""),
            str(participant.get("email") or ""),
        )
        if role in {"from", "sender", "author"}:
            continue
        if not role and participant_addresses and participant_addresses <= author_addresses:
            continue
        if not participant_addresses and not str(participant.get("name") or "").strip():
            continue
        recipients.append(participant)

    unique_recipient_keys: set[str] = set()
    user_is_recipient = False
    for index, participant in enumerate(recipients):
        participant_addresses = _email_addresses(
            str(participant.get("address") or ""),
            str(participant.get("email") or ""),
        )
        key = next(iter(participant_addresses), "") or str(
            participant.get("name") or index
        ).casefold()
        unique_recipient_keys.add(key)
        if _participant_is_user(participant, user_addresses, user_name_tokens):
            user_is_recipient = True

    addressed_by_name = bool(
        user_is_recipient
        and any(
            re.search(rf"(?<!\w){re.escape(token)}(?!\w)", normalized_body)
            for token in user_name_tokens
        )
    )
    return AssignmentSignals(
        directly_mentioned=directly_mentioned,
        user_is_recipient=user_is_recipient,
        sole_recipient=user_is_recipient and len(unique_recipient_keys) == 1,
        addressed_by_name=addressed_by_name,
    )
