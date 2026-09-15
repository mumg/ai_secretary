from improver.config import AppConfig, SourceConfig
from improver.connectors.base import SourceConnector
from improver.connectors.exchange import ExchangeConnector
from improver.connectors.external_tasks import ExternalTaskConnector
from improver.connectors.imap import ImapConnector
from improver.connectors.mts_link import MtsLinkConnector


def connector_for(source: SourceConfig, config: AppConfig) -> SourceConnector:
    if source.type == "imap":
        return ImapConnector(source, config)
    if source.type == "exchange":
        return ExchangeConnector(source, config)
    if source.type == "mts_link":
        return MtsLinkConnector(source, config)
    if source.type == "external_tasks":
        return ExternalTaskConnector(source, config)
    raise ValueError(f"Unsupported source type: {source.type}")


__all__ = ["SourceConnector", "connector_for"]
