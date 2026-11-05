import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, DateTime, Boolean, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./english_quest.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()

def utcnow(): return datetime.now(timezone.utc).replace(tzinfo=None)

class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True)
    name = Column(String(100), default="Ученик")
    username = Column(String(100), nullable=True)
    photo_url = Column(Text, nullable=True)
    xp = Column(Integer, default=0, nullable=False)
    coins = Column(Integer, default=50, nullable=False)
    streak = Column(Integer, default=0, nullable=False)
    last_learning_day = Column(String(10), nullable=True)
    premium_until = Column(DateTime, nullable=True)
    notifications = Column(Boolean, default=False)
    daily_goal = Column(Integer, default=50, nullable=False)
    joined_at = Column(DateTime, default=utcnow)

class Completion(Base):
    __tablename__ = "completions"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), index=True)
    lesson_id = Column(Integer, nullable=False)
    best_percent = Column(Integer, default=0)
    attempts = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("user_id", "lesson_id"),)

class Challenge(Base):
    __tablename__ = "challenges"
    id = Column(String(40), primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    mode = Column(String(40), nullable=False)
    lesson_id = Column(Integer, nullable=True)
    questions = Column(Text, nullable=False)
    answers = Column(Text, default="{}", nullable=False)
    created_at = Column(DateTime, default=utcnow)
    consumed = Column(Boolean, default=False)

class Attempt(Base):
    __tablename__ = "attempts"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), index=True)
    mode = Column(String(40), nullable=False)
    score = Column(Integer, nullable=False)
    earned_xp = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utcnow)

class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    word = Column(String(100), nullable=False)
    interval_days = Column(Integer, default=1)
    due_at = Column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("user_id", "word"),)

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(String(40), primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), index=True)
    stars = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utcnow)
    status = Column(String(16), default="pending")

class Payment(Base):
    __tablename__ = "payments"
    charge_id = Column(String(150), primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    invoice_id = Column(String(40), nullable=False)
    stars = Column(Integer, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    is_refunded = Column(Boolean, default=False)
    is_canceled = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)

class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
