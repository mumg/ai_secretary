from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from email.utils import getaddresses

from improver.config import IdentityConfig
from improver.enums import Direction
from improver.models import CommunicationEvent
from improver.services.text import clean_email_body

ASSIGNMENT_PROMPT = (
    "assignment_signals.eligible означает только возможность поручения пользователю, "
    "а не подтверждённого исполнителя каждой задачи. При eligible=false ставь assignee=other. "
    "Определяй исполнителя отдельно для каждого поручения. Совпадение только имени у тёзок "
    "не подтверждает личность. Сравни адреса, полные имена, авторов реплик, роли по проекту, "
    "вопросы и ответы в previous_events_in_thread. Чужой адрес не становится адресом пользователя "
    "из-за совпадения имени. Если пользователь единственный известный участник с указанным "
    "именем, обращение по этому имени допустимо. Если name_ambiguous=true, объясни разрешение "
    "неоднозначности точной цитатой в assignment_evidence и укажи assignee_address, если он "
    "известен из участников. Цитата должна связывать конкретного человека с этим поручением, "
    "а не просто упоминать его в подписи или приветствии. Учитывай последнее изменение "
    "исполнителя; не переносись между независимыми поручениями. Для сообщения «я сделаю» "
    "проверь автора. При неразрешённой неоднозначности ставь assignee=uncertain, даже если "
    "наличие самого поручения очевидно. При явном назначении другому человеку ставь other. "
)


@dataclass(frozen=True)
class AssignmentSignals:
    directly_mentioned: bool
    user_is_recipient: bool
    user_is_author: bool
    sole_recipient: bool
    addressed_by_name: bool
    name_ambiguous: bool = False
    ambiguous_names: tuple[str, ...] = ()
    unambiguous_names: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        # This is a coarse gate for asking Qwen to identify the assignee, not a
        # claim that the message already contains a task for the user. A user in
        # To/Cc can receive an assignment through the surrounding thread even
        # when the current message does not repeat their name.
        return self.directly_mentioned or self.user_is_recipient or self.user_is_author

    def model_dump(self) -> dict[str, object]:
        return {**asdict(self), "eligible": self.eligible}


def _email_addresses(*values: str) -> set[str]:
    return {address.casefold() for _, address in getaddresses(values) if "@" in address}


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[\w-]{2,}", value.casefold().replace("ё", "е")))


def _name_tokens(identity: IdentityConfig) -> set[str]:
    return set().union(*(_tokens(name) for name in identity.names))


def _has_name(text: str, name: str) -> bool:
    normalized = " ".join(text.casefold().replace("ё", "е").split())
    name = " ".join(name.casefold().replace("ё", "е").split())
    return bool(name and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", normalized))


def _addresses(participant: dict[str, object]) -> set[str]:
    return _email_addresses(
        str(participant.get("address") or ""), str(participant.get("email") or "")
    )


def _participant_is_user(participant, user_addresses, identity):
    addresses = _addresses(participant)
    if addresses:
        # A concrete foreign mailbox must never be overridden by a shared first name.
        return bool(addresses & user_addresses)
    name_tokens = _tokens(str(participant.get("name") or ""))
    return bool(name_tokens and any(_tokens(name) == name_tokens for name in identity.names))


def _author_participants(author: str) -> list[dict[str, object]]:
    if author and "@" not in author:
        return [{"name": author, "role": "author"}]
    return [
        {"name": name, "address": address, "role": "author"}
        for name, address in getaddresses([author])
        if name or address
    ]


def assignment_signals(
    event: CommunicationEvent,
    identity: IdentityConfig,
    conversation_context: list[dict[str, object]] | None = None,
) -> AssignmentSignals:
    body = event.body or ""
    user_addresses = {address.casefold() for address in identity.addresses}
    user_tokens = _name_tokens(identity)
    current = [p for p in (event.participants or []) if isinstance(p, dict)]
    roster = [*current, *_author_participants(event.author or "")]
    for previous in conversation_context or []:
        roster.extend(p for p in previous.get("participants", []) if isinstance(p, dict))
        # A synthetic summary has no participant identity.
        if previous.get("occurred_at"):
            roster.extend(_author_participants(str(previous.get("author") or "")))

    def person_key(p):
        addresses = _addresses(p)
        if addresses:
            return "user" if addresses & user_addresses else "email:" + sorted(addresses)[0]
        if p.get("external_id"):
            return "id:" + str(p["external_id"])
        # Merge addressless copies only when the full name identifies one known mailbox.
        same_name = {
            (
                "user"
                if _addresses(other) & user_addresses
                else "email:" + sorted(_addresses(other))[0]
            )
            for other in roster
            if _addresses(other)
            and _tokens(str(other.get("name") or "")) == _tokens(str(p.get("name") or ""))
        }
        if len(same_name) == 1:
            return next(iter(same_name))
        return "name:" + " ".join(sorted(_tokens(str(p.get("name") or ""))))

    known_user_keys = {
        person_key(p) for p in roster if _participant_is_user(p, user_addresses, identity)
    }
    potential_keys = {
        person_key(p)
        for p in roster
        if not _addresses(p)
        and _tokens(str(p.get("name") or ""))
        and _tokens(str(p.get("name") or "")) <= user_tokens
    }
    if len(potential_keys) == 1 and not any(
        _addresses(p)
        and not _addresses(p) & user_addresses
        and _tokens(str(p.get("name") or "")) & user_tokens
        for p in roster
    ):
        known_user_keys.update(potential_keys)
    # Two provider IDs with an identical configured name are still different people.
    name_only_ambiguous = len(known_user_keys - {"user"}) > 1
    competitors = [p for p in roster if person_key(p) not in known_user_keys or name_only_ambiguous]
    ambiguous = {
        token
        for token in user_tokens
        if any(token in _tokens(str(p.get("name") or "")) for p in competitors)
    }
    unique_names = {token for token in user_tokens if token not in ambiguous}
    unique_names.update(
        name
        for name in identity.names
        if len(_tokens(name)) > 1
        and not any(_tokens(name) <= _tokens(str(p.get("name") or "")) for p in competitors)
    )

    author_addresses = _email_addresses(event.author or "")
    user_is_author = event.direction == Direction.OUTGOING or bool(
        author_addresses & user_addresses
    )
    recipients = []
    for p in current:
        role = str(p.get("role") or "").casefold()
        if role in {"from", "sender", "author"}:
            continue
        if not role and _addresses(p) and _addresses(p) <= author_addresses:
            continue
        if _addresses(p) or str(p.get("name") or "").strip():
            recipients.append(p)
    user_is_recipient = any(_participant_is_user(p, user_addresses, identity) for p in recipients)
    # A short, addressless name may denote the user, but cannot establish their identity
    # when the roster contains a namesake. Let Qwen inspect it as a confirmation candidate.
    possible_user = any(
        not _addresses(p) and _tokens(str(p.get("name") or "")) & user_tokens for p in recipients
    )
    user_is_recipient = user_is_recipient or possible_user
    keys = {person_key(p) for p in recipients}
    names_in_body = {token for token in user_tokens if _has_name(body, token)}
    mentions = set(re.findall(r"(?<!\w)@([\w.-]+)", body.casefold()))
    local_parts = {address.split("@", 1)[0] for address in user_addresses}
    aliases = user_tokens | local_parts
    directly_mentioned = bool(mentions & aliases)
    ambiguous_mentions = mentions & ambiguous
    for mention in mentions & local_parts:
        if any(
            address.split("@", 1)[0] == mention and address not in user_addresses
            for p in roster
            for address in _addresses(p)
        ):
            ambiguous_mentions.add(mention)
    return AssignmentSignals(
        directly_mentioned=directly_mentioned,
        user_is_recipient=user_is_recipient,
        user_is_author=user_is_author,
        sole_recipient=user_is_recipient and len(keys) == 1 and not name_only_ambiguous,
        addressed_by_name=user_is_recipient and bool(names_in_body),
        name_ambiguous=bool(names_in_body & ambiguous or ambiguous_mentions or name_only_ambiguous),
        ambiguous_names=tuple(sorted(ambiguous | ambiguous_mentions)),
        unambiguous_names=tuple(sorted(unique_names)),
    )


def assignment_evidence_is_grounded(
    quote: str | None,
    signals: AssignmentSignals,
    identity: IdentityConfig,
    event: CommunicationEvent,
    conversation_context: list[dict[str, object]],
) -> bool:
    """Require an identity-bearing original quote before auto-assigning among namesakes.

    Qwen must still judge the semantic link to the particular task. A source-grounded
    identity is necessary, not a proof that the model understood the conversation.
    """
    if not quote or len(quote.strip()) < 8:
        return False

    def normalize(value: str) -> str:
        return " ".join(value.casefold().split())

    needle = normalize(quote)
    originals = [
        {
            "body": clean_email_body(event.body or "")
            if event.event_type == "email"
            else event.body or "",
            "author": event.author or "",
        },
        *(item for item in conversation_context if item.get("occurred_at")),
    ]
    for source in originals:
        if needle not in normalize(str(source.get("body") or "")):
            continue
        if any(_has_name(quote, name) for name in signals.unambiguous_names):
            return True
        quote_addresses = set(re.findall(r"[\w.+-]+@[\w.-]+\.\w+", quote.casefold()))
        if quote_addresses & set(a.casefold() for a in identity.addresses):
            return True
        if _email_addresses(str(source.get("author") or "")) & set(
            a.casefold() for a in identity.addresses
        ) and re.search(r"\b(?:я|беру|возьму|сделаю|подготовлю|отправлю)\b", quote.casefold()):
            return True
    return False
