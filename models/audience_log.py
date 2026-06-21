"""models/audience_log.py — Logs d'export audience (Chantier 19.B)"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from core.database import Base


class AudienceExportLog(Base):
    """
    Trace chaque export CSV d'audience par un organisateur.
    Sert de preuve juridique en cas de litige/fuite de données.
    """
    __tablename__ = "audience_export_logs"

    id              = Column(Integer, primary_key=True, index=True)
    organizer_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_id        = Column(Integer, ForeignKey("events.id", ondelete="SET NULL"), nullable=True)
    nb_lignes       = Column(Integer, nullable=False)
    ip_address      = Column(String, nullable=True)
    user_agent      = Column(String(500), nullable=True)
    filters_applied = Column(String(500), nullable=True)
    exported_at     = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_export_log_org", "organizer_id", "exported_at"),
        Index("idx_export_log_event", "event_id"),
    )

    organizer = relationship("User", foreign_keys=[organizer_id])
    event     = relationship("Event", foreign_keys=[event_id])
