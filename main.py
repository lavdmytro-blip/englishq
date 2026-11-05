"""English Quest: FastAPI Mini App + aiogram polling bot (one worker)."""
import asyncio
import hashlib
import hmac
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl

from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, PreCheckoutQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, LabeledPrice

from db import Base, engine, SessionLocal, User, Completion, Challenge, Attempt, Review, Invoice, Payment, Setting, utcnow
from curriculum import UNITS
from game import MODES, generate, public_question, check

TOKEN = os.getenv('BOT_TOKEN','').strip()
WEB_URL = os.getenv('MINI_APP_URL','').strip()
OWNER_ID = int(os.getenv('OWNER_TELEGRAM_ID','0'))
DEMO = os.getenv('DEMO_MODE','false').lower() == 'true'
PREMIUM_INITIAL = int(os.getenv('PREMIUM_STARS','149'))
PERIOD = 2_592_000
router = Router()
bot = Bot(token=TOKEN) if TOKEN else None
polling_task = None

def db_session():
    with SessionLocal() as db:
        yield db

def premium(user):
    return bool(user.premium_until and user.premium_until > utcnow())

def get_price(db):
    item = db.get(Setting, 'premium_stars')
    return int(item.value) if item else PREMIUM_INITIAL

def verify_init_data(raw: str):
    if not TOKEN or not raw:
        raise HTTPException(401, 'Открой приложение через Telegram')
    parts = dict(parse_qsl(raw, strict_parsing=True, keep_blank_values=True))
    received = parts.get('hash','')
    if not received:
        raise HTTPException(401, 'Отсутствует подпись Telegram')
    check_string = '\n'.join(f'{k}={v}' for k,v in sorted(parts.items()) if k != 'hash')
    secret = hmac.new(b'WebAppData', TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(received, expected):
        raise HTTPException(401, 'Неверная подпись Telegram')
    try:
        date = int(parts['auth_date'])
        if abs(datetime.now(timezone.utc).timestamp() - date) > 86400:
            raise ValueError('expired')
        data = json.loads(parts['user'])
        if not isinstance(data.get('id'), int):
            raise ValueError('missing user')
        return data
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(401, 'Данные авторизации устарели или некорректны') from exc

def current_user(request: Request, db: Session = Depends(db_session)):
    raw = request.headers.get('Authorization','')
    if DEMO and request.client and request.client.host in ('127.0.0.1','::1','testclient','localhost') and raw == 'demo':
        data = {'id':424242,'first_name':'Demo','username':'demo_learner'}
    elif raw.startswith('tma '):
        data = verify_init_data(raw[4:])
    else:
        raise HTTPException(401, 'Требуется Telegram Mini App')
    user = db.get(User, data['id'])
    if not user:
        user = User(id=data['id'], name=data.get('first_name','Ученик')[:100], username=data.get('username'), photo_url=data.get('photo_url'))
        db.add(user)
    else:
        user.name = data.get('first_name', user.name)[:100]
        user.username = data.get('username',user.username)
        user.photo_url = data.get('photo_url',user.photo_url)
    db.commit()
    return user

def profile(user, db):
    completed = db.query(Completion).filter_by(user_id=user.id).all()
    total_words = db.query(Review).filter_by(user_id=user.id).count()
    return {'id':user.id,'name':user.name,'username':user.username,'photo_url':user.photo_url,
            'xp':user.xp,'coins':user.coins,'streak':user.streak,'learned_words':total_words,
            'completed_lessons':sum(c.best_percent >= 60 for c in completed),
            'premium':premium(user),'premium_until':user.premium_until.isoformat() if user.premium_until else None,
            'notifications':user.notifications, 'daily_goal':user.daily_goal, 'admin':user.id == OWNER_ID and OWNER_ID != 0}

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if not db.get(Setting,'premium_stars'):
            db.add(Setting(key='premium_stars', value=str(PREMIUM_INITIAL))); db.commit()
    global polling_task
    if bot:
        dp = Dispatcher()
        dp.include_router(router)
        polling_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    yield
    if polling_task:
        polling_task.cancel()
        try: await polling_task
        except asyncio.CancelledError: pass
    if bot: await bot.session.close()

app = FastAPI(title='English Quest API', lifespan=lifespan)

@app.get('/api/health')
def health(): return {'ok': True, 'payments_configured':bool(TOKEN)}

@app.get('/api/bootstrap')
def bootstrap(user: User = Depends(current_user), db: Session = Depends(db_session)):
    comps = {c.lesson_id:c.best_percent for c in db.query(Completion).filter_by(user_id=user.id)}
    lessons = [{'id':i,'title':u['title'],'icon':u['icon'],'level':u['level'],
                'percent':comps.get(i,0), 'locked': (i > 0 and comps.get(i-1,0) < 60),
                'premium': False} for i,u in enumerate(UNITS)]
    leaderboard = [{'name':u.name,'username':u.username,'xp':u.xp,'id':u.id} for u in db.query(User).order_by(User.xp.desc()).limit(20)]
    today = utcnow().date()
    daily_xp=db.query(func.coalesce(func.sum(Attempt.earned_xp),0)).filter(Attempt.user_id==user.id,Attempt.created_at>=datetime.combine(today,datetime.min.time())).scalar()
    return {'me':profile(user,db),'lessons':lessons,'daily_xp':daily_xp,
            'modes':[dict(id=k,**v,premium=k in ('snake','memory')) for k,v in MODES.items()],
            'leaderboard':leaderboard,'price':get_price(db),'daily_claimed': bool(user.last_learning_day == today.isoformat()),
            'review_due':db.query(Review).filter(Review.user_id==user.id,Review.due_at<=utcnow()).count()}

class StartIn(BaseModel):
    kind: str
    lesson_id: int | None = None
    mode: str | None = None

@app.post('/api/play/start')
def play_start(body: StartIn, user: User = Depends(current_user), db: Session = Depends(db_session)):
    if body.kind == 'lesson':
        if body.lesson_id is None or not (0 <= body.lesson_id < len(UNITS)):
            raise HTTPException(400,'Неизвестный урок')
        if body.lesson_id > 0:
            prev = db.query(Completion).filter_by(user_id=user.id,lesson_id=body.lesson_id-1).first()
            if not prev or prev.best_percent < 60:
                raise HTTPException(403,'Сначала пройди предыдущий урок на 60%')
    elif body.kind == 'game':
        if body.mode not in MODES: raise HTTPException(400,'Неизвестная игра')
        if body.mode in ('snake','memory') and not premium(user) and not (DEMO and user.id == 424242):
            raise HTTPException(403,'Эта игра входит в Premium')
    else: raise HTTPException(400,'Выбери урок или игру')
    today = utcnow().date()
    count = db.query(Attempt).filter(Attempt.user_id==user.id,Attempt.created_at >= datetime.combine(today,datetime.min.time())).count()
    if count >= 100: raise HTTPException(429,'Лимит игровых сессий на сегодня')
    questions = generate(body.kind,body.lesson_id,body.mode)
    cid = secrets.token_hex(16)
    db.add(Challenge(id=cid,user_id=user.id,mode=('lesson' if body.kind=='lesson' else body.mode),
                     lesson_id=body.lesson_id if body.kind=='lesson' else None,
                     questions=json.dumps(questions,ensure_ascii=False),answers='{}'))
    db.commit()
    return {'id':cid,'title':UNITS[body.lesson_id]['title'] if body.kind=='lesson' else MODES[body.mode]['title'],
            'questions':[public_question(q) for q in questions]}

class AnswerIn(BaseModel):
    id: str = Field(max_length=40)
    index: int
    answer: str | dict[str,str] | None

@app.post('/api/play/answer')
def play_answer(body: AnswerIn, user: User = Depends(current_user), db: Session = Depends(db_session)):
    c = db.get(Challenge,body.id)
    if not c or c.user_id != user.id or c.consumed: raise HTTPException(404,'Сессия недоступна')
    if c.created_at < utcnow() - timedelta(hours=1): raise HTTPException(410,'Сессия устарела')
    questions = json.loads(c.questions); answers = json.loads(c.answers)
    if not (0 <= body.index < len(questions)): raise HTTPException(400,'Неверный вопрос')
    key = str(body.index)
    if key not in answers:
        result = check(questions[body.index],body.answer)
        answers[key] = {'answer':body.answer,'ok':result}
        c.answers = json.dumps(answers,ensure_ascii=False)
        db.commit()
    return {'ok':answers[key]['ok'],'correct':questions[body.index]['correct'],
            'explanation':questions[body.index]['explanation']}

class FinishIn(BaseModel):
    id: str = Field(max_length=40)

@app.post('/api/play/finish')
def play_finish(body: FinishIn, user: User = Depends(current_user), db: Session = Depends(db_session)):
    c = db.get(Challenge,body.id)
    if not c or c.user_id != user.id or c.consumed: raise HTTPException(404,'Эта сессия уже завершена')
    if c.created_at < utcnow() - timedelta(hours=1): raise HTTPException(410,'Сессия устарела')
    questions = json.loads(c.questions); answers = json.loads(c.answers)
    if len(answers) != len(questions): raise HTTPException(400,'Ответь на все вопросы')
    score = sum(bool(a['ok']) for a in answers.values())
    percent = round(score/len(questions)*100)
    now = utcnow(); today = now.date().isoformat()
    first = False
    if c.mode == 'lesson':
        comp = db.query(Completion).filter_by(user_id=user.id,lesson_id=c.lesson_id).first()
        if not comp:
            comp = Completion(user_id=user.id,lesson_id=c.lesson_id)
            db.add(comp)
            first = True
        elif comp.best_percent < 60 and percent >= 60:
            first = True
        comp.best_percent = max(comp.best_percent,percent)
        comp.attempts += 1
    attempts_today = db.query(Attempt).filter(Attempt.user_id==user.id,Attempt.created_at >= datetime.combine(now.date(),datetime.min.time())).count()
    earned = (score * 5 + (30 if first and percent>=60 else 0)) if attempts_today < 15 else 0
    coins = min(20,score*2) if attempts_today < 15 else 0
    user.xp += earned; user.coins += coins
    if user.last_learning_day != today and score > 0:
        prev_day = (now.date()-timedelta(days=1)).isoformat()
        user.streak = user.streak+1 if user.last_learning_day==prev_day else 1
        user.last_learning_day = today
    if c.mode == 'lesson' and percent>=60:
        unit = UNITS[c.lesson_id]
        for en,ru in unit['words']:
            row = db.query(Review).filter_by(user_id=user.id,word=en).first()
            if not row:
                row = Review(user_id=user.id,word=en,interval_days=1,due_at=now+timedelta(days=1))
                db.add(row)
            else:
                row.interval_days = min(row.interval_days*2 if percent>=70 else 1,60)
                row.due_at = now+timedelta(days=row.interval_days)
    db.add(Attempt(user_id=user.id,mode=c.mode,score=percent,earned_xp=earned))
    c.consumed=True
    db.commit()
    return {'score':score,'total':len(questions),'percent':percent,'earned_xp':earned,
            'earned_coins':coins,'first_pass':first and percent>=60,'me':profile(user,db)}

@app.get('/api/vocabulary')
def vocabulary(user: User = Depends(current_user),db: Session = Depends(db_session)):
    known={r.word:r for r in db.query(Review).filter_by(user_id=user.id)}
    result=[]
    for u in UNITS:
        for en,ru in u['words']:
            if en in known:
                r=known[en]
                result.append({'en':en,'ru':ru,'due':r.due_at<=utcnow(),'interval':r.interval_days})
    return {'words':result}

@app.post('/api/vocabulary/review/{word}')
def review_word(word: str, user: User = Depends(current_user),db: Session = Depends(db_session)):
    row=db.query(Review).filter_by(user_id=user.id,word=word).first()
    if not row: raise HTTPException(404,'Слово ещё не изучено')
    row.interval_days=min(row.interval_days*2,60)
    row.due_at=utcnow()+timedelta(days=row.interval_days)
    db.commit()
    return {'ok':True}

class NotificationIn(BaseModel):
    enabled: bool

@app.post('/api/settings/notifications')
def save_notifications(body: NotificationIn,user: User = Depends(current_user), db: Session = Depends(db_session)):
    user.notifications=body.enabled;db.commit();return {'ok':True}

class GoalIn(BaseModel):
    xp: int = Field(ge=10,le=500)

@app.post('/api/settings/goal')
def save_goal(body: GoalIn,user: User = Depends(current_user),db: Session = Depends(db_session)):
    user.daily_goal=body.xp;db.commit();return {'xp':body.xp}

@app.post('/api/premium/invoice')
async def premium_invoice(user: User = Depends(current_user), db: Session = Depends(db_session)):
    if not bot: raise HTTPException(503,'Владелец пока не подключил Telegram-бота')
    price=get_price(db)
    invoice=Invoice(id=secrets.token_hex(16), user_id=user.id, stars=price)
    db.add(invoice);db.commit()
    try:
        link=await bot.create_invoice_link(title='English Quest Premium',
                description='30 дней расширенных уроков, игр и статистики. Автопродление каждые 30 дней.',
                payload='eq:'+invoice.id, provider_token='', currency='XTR',
                prices=[LabeledPrice(label='Premium / 30 дней',amount=price)],subscription_period=PERIOD)
        return {'url':link,'invoice_id':invoice.id}
    except Exception:
        db.delete(invoice);db.commit()
        raise HTTPException(502,'Не удалось создать счёт. Проверь настройки Telegram')

@app.post('/api/premium/cancel')
async def premium_cancel(user: User = Depends(current_user), db: Session = Depends(db_session)):
    payment=db.query(Payment).filter_by(user_id=user.id,is_refunded=False,is_canceled=False).order_by(Payment.created_at.desc()).first()
    if not payment or not bot: raise HTTPException(400,'Активная подписка не найдена')
    try:
        await bot.edit_user_star_subscription(user_id=user.id,telegram_payment_charge_id=payment.charge_id,is_canceled=True)
        payment.is_canceled=True;db.commit()
        return {'ok':True,'note':'Автопродление отключено; Premium действует до оплаченной даты.'}
    except Exception:
        raise HTTPException(502,'Не удалось отключить автопродление через Telegram')

@app.get('/api/admin/stats')
def admin_stats(user: User = Depends(current_user),db: Session = Depends(db_session)):
    if not OWNER_ID or user.id != OWNER_ID: raise HTTPException(403)
    return {'users':db.query(User).count(),'attempts':db.query(Attempt).count(),
            'premium_users':db.query(User).filter(User.premium_until>utcnow()).count(),
            'payments':db.query(Payment).filter_by(is_refunded=False).count(),
            'stars_received':db.query(func.coalesce(func.sum(Payment.stars),0)).filter(Payment.is_refunded==False).scalar(),
            'price':get_price(db)}

class PriceIn(BaseModel):
    stars: int = Field(ge=1,le=10000)

@app.post('/api/admin/price')
def admin_price(body: PriceIn,user: User = Depends(current_user),db: Session = Depends(db_session)):
    if not OWNER_ID or user.id != OWNER_ID: raise HTTPException(403)
    item=db.get(Setting,'premium_stars');item.value=str(body.stars);db.commit();return {'stars':body.stars}

@router.message(CommandStart())
async def start(message: Message):
    if not WEB_URL or not WEB_URL.startswith('https://'):
        return await message.answer('English Quest пока настраивается. Администратору нужно указать MINI_APP_URL (HTTPS).')
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🎓 Открыть English Quest',web_app=WebAppInfo(url=WEB_URL))]])
    await message.answer('👋 Добро пожаловать в English Quest! Учись играть: уроки, мини-игры и XP.\n\nНажми кнопку, чтобы начать.',reply_markup=kb)

@router.message(Command('paysupport'))
async def paysupport(message: Message):
    await message.answer('По вопросам платежей Stars напиши владельцу бота. Для отключения продления открой раздел Premium → Управление подпиской.')

@router.pre_checkout_query()
async def on_checkout(query: PreCheckoutQuery):
    parts=query.invoice_payload.split(':',1)
    good=False
    if len(parts)==2 and parts[0]=='eq' and query.currency=='XTR':
        with SessionLocal() as db:
            invoice=db.get(Invoice,parts[1])
            good=bool(invoice and invoice.user_id==query.from_user.id and invoice.stars==query.total_amount and invoice.created_at>=utcnow()-timedelta(days=35))
    await query.answer(ok=good,error_message=None if good else 'Счёт не найден или сумма не совпадает')

@router.message(F.successful_payment)
async def on_payment(message: Message):
    p=message.successful_payment
    if not p or not message.from_user or p.currency!='XTR' or not p.invoice_payload.startswith('eq:'):
        return
    with SessionLocal() as db:
        invoice=db.get(Invoice,p.invoice_payload[3:])
        if not invoice or invoice.user_id!=message.from_user.id or invoice.stars!=p.total_amount:
            return
        if db.get(Payment,p.telegram_payment_charge_id): return
        until=datetime.fromtimestamp(p.subscription_expiration_date,timezone.utc).replace(tzinfo=None) if p.subscription_expiration_date else utcnow()+timedelta(seconds=PERIOD)
        user=db.get(User,invoice.user_id)
        if not user: return
        db.add(Payment(charge_id=p.telegram_payment_charge_id,user_id=user.id,invoice_id=invoice.id,stars=p.total_amount,expires_at=until))
        user.premium_until=max(filter(None,[user.premium_until,until]))
        invoice.status='paid';db.commit()
    await message.answer('🌟 Premium активирован! Открой приложение — новые возможности доступны.')

@router.message(F.refunded_payment)
async def on_refund(message: Message):
    p=message.refunded_payment
    with SessionLocal() as db:
        payment=db.get(Payment,p.telegram_payment_charge_id)
        if not payment or payment.user_id!=message.from_user.id: return
        payment.is_refunded=True
        valid=db.query(Payment).filter(Payment.user_id==payment.user_id,Payment.is_refunded==False,Payment.expires_at>utcnow()).all()
        user=db.get(User,payment.user_id)
        if user: user.premium_until=max([x.expires_at for x in valid],default=None)
        db.commit()

# Serve the Vite build from the same origin in production (npm run build first).
DIST=Path(__file__).resolve().parents[1]/'frontend'/'dist'
if DIST.joinpath('assets').exists():
    app.mount('/assets', StaticFiles(directory=str(DIST/'assets')), name='assets')
@app.get('/')
@app.get('/{path:path}')
def spa(path: str=''):
    if path.startswith('api/') or not (DIST/'index.html').exists(): raise HTTPException(404)
    candidate=(DIST/path).resolve()
    if candidate.is_file() and DIST.resolve() in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(DIST/'index.html')
