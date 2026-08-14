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


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

# Database configuration
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///referral.db"

db = SQLAlchemy(app)
migrate = Migrate(app, db)

class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    openid = db.Column(db.String(255), unique=True, nullable=True)
    invite_code = db.Column(db.String(20), unique=True, nullable=False)
    balance = db.Column(
        db.Numeric(10, 2),
        default=Decimal("0.00"),
        nullable=False
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

        
def create_member(openid=None):
    if openid:
        existing_member = Member.query.filter_by(openid=openid).first()

        if existing_member:
            return existing_member

    invite_code = generate_invite_code()

    member = Member(
        openid=openid,
        invite_code=invite_code
    )

    db.session.add(member)
    db.session.commit()

    return member


def add_referral(member, amount, reward_rate=Decimal("0.05")):
    reward = (amount * reward_rate).quantize(Decimal("0.01"))

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


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        member = create_member()

        return (
            f"Registration successful! "
            f"Your referral code is: {member.invite_code}"
        )

    return render_template("register.html")


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
        except (TypeError, ValueError, InvalidOperation):
            flash("Please enter a valid customer spend amount.", "error")
            return redirect(url_for("staff"))

        if amount <= 0:
            flash("Customer spend must be greater than £0.", "error")
            return redirect(url_for("staff"))

        member = Member.query.filter_by(invite_code=invite_code).first()

        if not member:
            flash("Referral code not found.", "error")
            return redirect(url_for("staff"))

        referral = add_referral(member, amount)

        flash(
            f"Referral added successfully! "
            f"Reward: £{referral.reward:.2f} | "
            f"New balance: £{member.balance:.2f}",
            "success"
        )

        return redirect(url_for("staff"))

    referrals = Referral.query.order_by(Referral.created_at.desc()).all()
    return render_template(
        "staff.html",
        referrals=referrals,
        invite_code=invite_code
    )


@app.route("/members")
def members():
    invite_code = request.args.get("invite_code")

    member = None
    referrals = []
    withdrawals = []
    qr_code = None

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

            member_url = (
                f"http://192.168.1.113:5000/member/"
                f"{member.invite_code}"
            )

            qr_code = generate_qr_code(member_url)

    return render_template(
        "members.html",
        member=member,
        referrals=referrals,
        withdrawals=withdrawals,
        total_referrals=total_referrals,
        total_spend=total_spend,
        total_rewards=total_rewards,
        pending_total=pending_total,
        available_balance=available_balance,
        qr_code=qr_code
    )


@app.route("/withdraw", methods=["GET", "POST"])
def withdraw():
    if request.method == "POST":
        invite_code = request.form.get("invite_code")
        amount_text = request.form.get("amount")

        member = Member.query.filter_by(invite_code=invite_code).first()

        if member is None:
            flash("Referral code not found.", "error")
            return redirect(url_for("withdraw"))

        try:
            amount = Decimal(amount_text)
            withdrawal = create_withdrawal(member, amount)
        except (TypeError, ValueError, InvalidOperation) as error:
            flash(str(error), "error")
            return redirect(url_for("withdraw"))

        flash(
            f"Withdrawal request submitted successfully! "
            f"Amount: £{withdrawal.amount:.2f}",
            "success"
        )

        return redirect(url_for("withdraw"))

    return render_template("withdraw.html")


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

            return redirect(url_for("staff"))

        flash("Incorrect password.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("staff_logged_in", None)
    return redirect(url_for("login"))


@app.route("/member/<invite_code>")
def member_page(invite_code):
    member = Member.query.filter_by(invite_code=invite_code).first()

    if member is None:
        return "Member not found", 404

    referrals = Referral.query.filter_by(
        member_id=member.id
    ).all()

    withdrawals = Withdrawal.query.filter_by(
        member_id=member.id
    ).all()

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
        withdrawals=withdrawals,
        total_rewards=total_rewards,
        pending_total=pending_total,
        available_balance=available_balance
    )


@app.route("/member/<invite_code>/withdraw", methods=["POST"])
def member_withdraw(invite_code):
    member = Member.query.filter_by(invite_code=invite_code).first()

    if member is None:
        return "Member not found", 404

    amount_text = request.form.get("amount")

    try:
        amount = Decimal(amount_text)
        create_withdrawal(member, amount)

    except (TypeError, ValueError, InvalidOperation) as error:
        flash(str(error), "error")
        return redirect(
            url_for("member_page", invite_code=invite_code)
        )

    flash(
        f"Withdrawal request submitted successfully! Amount: £{amount:.2f}",
        "success"
    )

    return redirect(
        url_for("member_page", invite_code=invite_code)
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)