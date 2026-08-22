import os
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
import random
import string
import qrcode
import io
import base64
import secrets


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

# Database configuration
database_url = os.getenv("DATABASE_URL")

if database_url:
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///referral.db"

db = SQLAlchemy(app)
migrate = Migrate(app, db)

class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    nickname = db.Column(
        db.String(100),
        nullable=True
    )

    phone = db.Column(
        db.String(30),
        nullable=True
    )

    wechat = db.Column(
        db.String(100),
        nullable=True
    )

    openid = db.Column(
        db.String(255), 
        unique=True, 
        nullable=True
    )

    invite_code = db.Column(
        db.String(20), 
        unique=True, 
        nullable=False
    )

    referral_token = db.Column(
        db.String(64),
        unique=True,
        nullable=True
    )

    access_token = db.Column(
        db.String(64),
        unique=True,
        nullable=True
    )

    balance = db.Column(
        db.Numeric(10, 2),
        default=Decimal("0.00"),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=True
    )

    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True
    )


class Referral(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey("member.id"), nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    reward = db.Column(db.Numeric(10, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class Withdrawal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(
        db.Integer,
        db.ForeignKey("member.id"),
        nullable=False
    )
    member = db.relationship("Member")
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), default="pending", nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    paid_at = db.Column(db.DateTime, nullable=True)


def generate_invite_code():
    letters = string.ascii_uppercase
    numbers = string.digits
    characters = letters + numbers

    while True:
        code = "".join(random.choices(characters, k=6))

        existing_member = Member.query.filter_by(invite_code=code).first()

        if existing_member is None:
            return code


def generate_token():
    return secrets.token_urlsafe(32)


def fill_missing_member_tokens():
    members = Member.query.all()
    updated_count = 0

    for member in members:
        updated = False

        if not member.referral_token:
            member.referral_token = generate_token()
            updated = True

        if not member.access_token:
            member.access_token = generate_token()
            updated = True

        if updated:
            updated_count += 1

    db.session.commit()

    return updated_count

        
def create_member(openid=None, nickname=None, phone=None, wechat=None):
    if openid:
        existing_member = Member.query.filter_by(openid=openid).first()

        if existing_member:
            return existing_member

    invite_code = generate_invite_code()
    referral_token = generate_token()
    access_token = generate_token()

    member = Member(
        openid=openid,
        nickname=nickname,
        phone=phone,
        wechat=wechat,
        invite_code=invite_code,
        referral_token=referral_token,
        access_token=access_token
    )

    db.session.add(member)
    db.session.commit()

    return member


def add_referral(member, amount, reward):
    amount = amount.quantize(Decimal("0.01"))
    reward = reward.quantize(Decimal("0.01"))

    referral = Referral(
        member_id=member.id,
        amount=amount,
        reward=reward
    )

    member.balance += reward

    db.session.add(referral)
    db.session.commit()

    return referral

def generate_qr_code(invite_code):
    qr = qrcode.make(invite_code)

    buffer = io.BytesIO()
    qr.save(buffer, format="PNG")

    qr_base64 = base64.b64encode(
        buffer.getvalue()
    ).decode("utf-8")

    return qr_base64


def create_withdrawal(member, amount):
    pending_total = sum(
        (
            withdrawal.amount
            for withdrawal in Withdrawal.query.filter_by(
                member_id=member.id,
                status="pending"
            ).all()
        ),
        Decimal("0.00")
    )

    available_balance = member.balance - pending_total

    if amount <= 0:
        raise ValueError("Withdrawal amount must be greater than £0.")

    if amount > available_balance:
        raise ValueError("Insufficient available balance.")

    withdrawal = Withdrawal(
        member_id=member.id,
        amount=amount,
        status="pending"
    )

    db.session.add(withdrawal)
    db.session.commit()

    return withdrawal


def mark_withdrawal_paid(withdrawal):
    if withdrawal.status == "paid":
        raise ValueError("Withdrawal has already been paid.")

    member = db.session.get(Member, withdrawal.member_id)

    if member is None:
        raise ValueError("Member not found.")

    if member.balance < withdrawal.amount:
        raise ValueError("Insufficient member balance.")

    member.balance -= withdrawal.amount

    withdrawal.status = "paid"
    withdrawal.paid_at = datetime.now(timezone.utc)

    db.session.commit()

    return withdrawal


def format_uk_time(dt):
    if dt is None:
        return "-"

    uk_time = dt.replace(
        tzinfo=timezone.utc
    ).astimezone(
        ZoneInfo("Europe/London")
    )

    return uk_time.strftime("%d %b %Y, %H:%M")

app.jinja_env.globals["format_uk_time"] = format_uk_time


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/staff", methods=["GET", "POST"])
def staff():
    if not session.get("staff_logged_in"):
        return redirect(
            url_for(
                "login",
                next=request.url
            )
        )
    
    invite_code = request.args.get("invite_code")

    if request.method == "POST":
        invite_code = request.form.get("invite_code")

        try:
            amount = Decimal(request.form["amount"])
            reward = Decimal(request.form["reward"])
        except (TypeError, ValueError, InvalidOperation):
            flash("请输入正确的消费金额和返利金额。", "error")
            return redirect(
                url_for(
                    "staff",
                    invite_code=invite_code
                )
            )

        if amount <= 0:
            flash("客户消费金额必须大于 £0。", "error")
            return redirect(
                url_for(
                    "staff",
                    invite_code=invite_code
                )
            )

        if reward < 0:
            flash("返利金额不能小于 ¥0。", "error")
            return redirect(
                url_for(
                    "staff",
                    invite_code=invite_code
                )
            )

        member = Member.query.filter_by(invite_code=invite_code).first()

        if not member:
            flash("Referral code not found.", "error")
            return redirect(url_for("staff"))

        if not member.is_active:
            flash("该会员已停用，无法录入新的返利。", "error")
            return redirect(url_for("staff"))

        referral = add_referral(member, amount, reward)

        flash(
            f"返利添加成功！"
            f"本次返利：¥{referral.reward:.2f} | "
            f"会员当前余额：¥{member.balance:.2f}",
            "success"
        ) 

        return redirect(
            url_for(
                "staff",
                invite_code=invite_code
            )
        )

    referrals = []
    member = None

    if invite_code:
        member = Member.query.filter_by(
            invite_code=invite_code
        ).first()

        if member:
            referrals = Referral.query.filter_by(
                member_id=member.id
            ).order_by(
                Referral.created_at.desc()
            ).all()

    return render_template(
        "staff.html",
        referrals=referrals,
        invite_code=invite_code,
        member = member
    )


@app.route("/members")
def members():
    if not session.get("staff_logged_in"):
        return redirect(url_for("login"))

    invite_code = request.args.get("invite_code")
    query = request.args.get("q", "").strip()

    member = None
    referrals = []
    withdrawals = []
    referral_qr = None
    member_private_url = None

    search_results = []

    if query:
        filters = [
            Member.nickname.ilike(f"%{query}%"),
            Member.phone.ilike(f"%{query}%"),
            Member.wechat.ilike(f"%{query}%")
        ]

        if query.isdigit():
            filters.append(Member.id == int(query))

        search_results = Member.query.filter(
            db.or_(*filters)
        ).order_by(Member.id.desc()).all()

    total_referrals = 0
    total_spend = Decimal("0.00")
    total_rewards = Decimal("0.00")
    pending_total = Decimal("0.00")
    available_balance = Decimal("0.00")

    if invite_code:
        member = Member.query.filter_by(invite_code=invite_code).first()

        if member:
            referrals = Referral.query.filter_by(
                member_id=member.id
            ).order_by(Referral.created_at.desc()).all()
            withdrawals = Withdrawal.query.filter_by(
                member_id=member.id
            ).order_by(Withdrawal.created_at.desc()).all()
            referral_url = url_for(
                "referral_lookup",
                referral_token=member.referral_token,
                _external=True
            )

            referral_qr = generate_qr_code(referral_url)

            member_private_url = url_for(
                "member_portal",
                access_token=member.access_token,
                _external=True
            )

            total_referrals = len(referrals)
            total_spend = sum(referral.amount for referral in referrals)
            total_rewards = sum(referral.reward for referral in referrals)
            pending_total = sum(
                (
                    withdrawal.amount
                    for withdrawal in Withdrawal.query.filter_by(
                        member_id=member.id,
                        status="pending"
                    ).all()
                ),
                Decimal("0.00")
            )
            available_balance = member.balance - pending_total

    return render_template(
        "members.html",
        member=member,
        referrals=referrals,
        referral_qr=referral_qr,
        member_private_url=member_private_url,
        search_results=search_results,
        query=query,
        withdrawals=withdrawals,
        total_referrals=total_referrals,
        total_spend=total_spend,
        total_rewards=total_rewards,
        pending_total=pending_total,
        available_balance=available_balance
    )


@app.route("/members/manage")
def manage_members():
    if not session.get("staff_logged_in"):
        return redirect(url_for("login"))

    keyword = request.args.get("q", "").strip()
    status = request.args.get("status", "all")
    balance_filter = request.args.get("balance", "all")
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    query = Member.query

    # 关键词搜索
    if keyword:
        filters = [
            Member.nickname.ilike(f"%{keyword}%"),
            Member.phone.ilike(f"%{keyword}%"),
            Member.wechat.ilike(f"%{keyword}%")
        ]

        if keyword.isdigit():
            filters.append(Member.id == int(keyword))

        query = query.filter(db.or_(*filters))

    # 状态筛选
    if status == "active":
        query = query.filter(Member.is_active.is_(True))

    elif status == "inactive":
        query = query.filter(Member.is_active.is_(False))

    # 余额筛选
    if balance_filter == "positive":
        query = query.filter(Member.balance > 0)

    elif balance_filter == "zero":
        query = query.filter(Member.balance == 0)

    # 创建日期筛选
    if date_from:
        try:
            start_date = datetime.strptime(
                date_from,
                "%Y-%m-%d"
            )
            query = query.filter(
                Member.created_at >= start_date
            )
        except ValueError:
            pass

    if date_to:
        try:
            end_date = datetime.strptime(
                date_to,
                "%Y-%m-%d"
            )

            end_date = end_date.replace(
                hour=23,
                minute=59,
                second=59
            )

            query = query.filter(
                Member.created_at <= end_date
            )
        except ValueError:
            pass

    members = query.order_by(
        Member.id.desc()
    ).all()

    return render_template(
        "manage_members.html",
        members=members,
        keyword=keyword,
        status=status,
        balance_filter=balance_filter,
        date_from=date_from,
        date_to=date_to
    )


@app.route("/members/<int:member_id>/toggle-active", methods=["POST"])
def toggle_member_active(member_id):
    if not session.get("staff_logged_in"):
        return redirect(url_for("login"))

    member = db.session.get(Member, member_id)

    if not member:
        flash("会员不存在。", "error")
        return redirect(url_for("manage_members"))

    member.is_active = not member.is_active
    db.session.commit()

    if member.is_active:
        flash(
            f"会员 #{member.id} 已恢复使用。",
            "success"
        )
    else:
        flash(
            f"会员 #{member.id} 已停用。",
            "success"
        )

    return redirect(url_for("manage_members"))


@app.route("/members/bulk-status", methods=["POST"])
def bulk_member_status():
    if not session.get("staff_logged_in"):
        return redirect(url_for("login"))

    member_ids = request.form.getlist("member_ids")
    action = request.form.get("action")

    if not member_ids:
        flash("请至少选择一个会员。", "error")
        return redirect(url_for("manage_members"))

    if action not in ["deactivate", "activate"]:
        flash("无效的批量操作。", "error")
        return redirect(url_for("manage_members"))

    members = Member.query.filter(
        Member.id.in_(member_ids)
    ).all()

    if action == "deactivate":
        for member in members:
            member.is_active = False

        message = f"已停用 {len(members)} 位会员。"

    else:
        for member in members:
            member.is_active = True

        message = f"已恢复 {len(members)} 位会员。"

    db.session.commit()

    flash(message, "success")

    return redirect(url_for("manage_members"))


@app.route("/members/create", methods=["GET", "POST"])
def create_member_page():
    if not session.get("staff_logged_in"):
        return redirect(url_for("login"))

    if request.method == "POST":
        nickname = request.form.get("nickname", "").strip()
        phone = request.form.get("phone", "").strip()
        wechat = request.form.get("wechat", "").strip()

        if not nickname:
            return "请输入会员昵称", 400
        
        member = create_member(
            nickname=nickname,
            phone=phone,
            wechat=wechat
        )

        referral_url = url_for(
            "referral_lookup",
            referral_token=member.referral_token,
            _external=True
        )

        referral_qr = generate_qr_code(referral_url)

        return render_template(
            "member_created.html",
            member=member,
            referral_url=referral_url,
            referral_qr=referral_qr
        )

    return render_template("create_member.html")


@app.route("/my/<access_token>")
def member_portal(access_token):
    member = Member.query.filter_by(
        access_token=access_token
    ).first()

    if not member:
        return "查询链接无效或已失效", 404

    if not member.is_active:
        return render_template(
            "member_inactive.html",
            member=member
        )

    referrals = Referral.query.filter_by(
        member_id=member.id
    ).order_by(Referral.created_at.desc()).all()

    withdrawals = Withdrawal.query.filter_by(
        member_id=member.id
    ).order_by(Withdrawal.created_at.desc()).all()

    referral_url = url_for(
        "referral_lookup",
        referral_token=member.referral_token,
        _external=True
    )

    referral_qr = generate_qr_code(referral_url)

    total_rewards = sum(
        (referral.reward for referral in referrals),
        Decimal("0.00")
    )

    pending_total = sum(
        (
            withdrawal.amount
            for withdrawal in withdrawals
            if withdrawal.status == "pending"
        ),
        Decimal("0.00")
    )

    available_balance = member.balance - pending_total

    return render_template(
        "member.html",
        member=member,
        referrals=referrals,
        referral_qr=referral_qr,
        withdrawals=withdrawals,
        total_rewards=total_rewards,
        pending_total=pending_total,
        available_balance=available_balance
    )


@app.route("/my/<access_token>/withdraw", methods=["POST"])
def member_withdraw(access_token):
    member = Member.query.filter_by(
        access_token=access_token
    ).first()

    if member is None:
        return "查询链接无效或已失效", 404

    if not member.is_active:
        return render_template(
            "member_inactive.html",
            member=member
        )

    amount_text = request.form.get("amount")

    try:
        amount = Decimal(amount_text)
        create_withdrawal(member, amount)

    except (TypeError, ValueError, InvalidOperation) as error:
        flash(str(error), "error")

        return redirect(
            url_for(
                "member_portal",
                access_token=access_token
            )
        )

    flash(
        f"提现申请已提交：¥{amount:.2f}",
        "success"
    )

    return redirect(
        url_for(
            "member_portal",
            access_token=access_token
        )
    )


@app.route("/ref/<referral_token>")
def referral_lookup(referral_token):
    member = Member.query.filter_by(
        referral_token=referral_token
    ).first()

    if not member:
        return "推荐二维码无效", 404

    if session.get("staff_logged_in"):
        return render_template(
            "referral_scan_result.html",
            member=member
        )

    return render_template("store_guide.html")


@app.route("/withdrawals")
def withdrawals():
    if not session.get("staff_logged_in"):
        return redirect(url_for("login"))

    pending_withdrawals = Withdrawal.query.filter_by(
        status="pending"
    ).order_by(Withdrawal.created_at.asc()).all()

    paid_withdrawals = Withdrawal.query.filter_by(
        status="paid"
    ).order_by(Withdrawal.paid_at.desc()).all()

    return render_template(
        "withdrawals.html",
        withdrawals=pending_withdrawals,
        paid_withdrawals=paid_withdrawals
    )


@app.route("/withdrawals/<int:withdrawal_id>/pay", methods=["POST"])
def pay_withdrawal(withdrawal_id):
    if not session.get("staff_logged_in"):
        return redirect(url_for("login"))

    withdrawal = db.session.get(Withdrawal, withdrawal_id)

    if withdrawal is None:
        flash("Withdrawal not found.", "error")
        return redirect(url_for("withdrawals"))

    try:
        mark_withdrawal_paid(withdrawal)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("withdrawals"))

    flash(
        f"Withdrawal #{withdrawal.id} marked as paid successfully.",
        "success"
    )

    return redirect(url_for("withdrawals"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password")

        if password == os.getenv("STAFF_PASSWORD"):
            session["staff_logged_in"] = True

            next_url = request.args.get("next")

            if next_url:
                return redirect(next_url)

            return redirect(url_for("dashboard"))

        flash("Incorrect password.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("staff_logged_in", None)
    return redirect(url_for("login"))


@app.route("/poster/<referral_token>")
def referral_poster(referral_token):
    member = Member.query.filter_by(
        referral_token=referral_token
    ).first()

    if not member:
        return "推荐二维码无效", 404

    referral_url = url_for(
        "referral_lookup",
        referral_token=member.referral_token,
        _external=True
    )

    referral_qr = generate_qr_code(referral_url)

    return render_template(
        "poster.html",
        member=member,
        referral_qr=referral_qr
    )


@app.route("/dashboard")
def dashboard():
    if not session.get("staff_logged_in"):
        return redirect(url_for("login"))

    return render_template("dashboard.html")


@app.route("/scan")
def scan():
    if not session.get("staff_logged_in"):
        return redirect(url_for("login"))

    return render_template("scan.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)