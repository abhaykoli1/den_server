"""Pydantic v2 request models (422 -> readable 400 via exception handler)."""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class In(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------- auth
class GoogleIn(In):
    # React GIS button deta hai `credential`; hamara app/web `idToken` — dono chalenge.
    idToken: Optional[str] = Field(default=None, max_length=4096)
    credential: Optional[str] = Field(default=None, max_length=4096)


class DevLoginIn(In):
    email: str = Field(min_length=3, max_length=120)
    name: Optional[str] = Field(default=None, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=20)
    location: Optional[str] = Field(default=None, max_length=120)


class MePatchIn(In):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=20)
    location: Optional[str] = Field(default=None, max_length=120)


# --------------------------------------------------------------------- clubs
class ClubIn(In):
    name: str = Field(min_length=2, max_length=80)


class ClubPatchIn(In):
    name: Optional[str] = Field(default=None, min_length=2, max_length=80)
    logo: Optional[str] = None  # data-url, size-checked server side


class ClubSettingsIn(In):
    winnerBonus: Optional[float] = Field(default=None, ge=0, le=100000)
    dueLimit: Optional[float] = Field(default=None, ge=0, le=1000000)
    defaultAdvance: Optional[float] = Field(default=None, ge=0, le=100000)
    monthlyTableDiscount: Optional[float] = Field(default=None, ge=0, le=100)


# --------------------------------------------------------------------- tables
class TableRateIn(In):
    hourlyRate: float = Field(ge=0, le=100000)
    ratesByPlayers: Optional[dict] = None          # {"2": 240, "4": 300}
    minCharge: Optional[float] = Field(default=0, ge=0, le=100000)
    peakHourlyRate: Optional[float] = Field(default=None, ge=0, le=100000)
    peakStartHour: Optional[int] = Field(default=None, ge=0, le=23)
    peakEndHour: Optional[int] = Field(default=None, ge=0, le=23)
    glovePrice: Optional[float] = Field(default=0, ge=0, le=10000)


class TableIn(In):
    name: str = Field(min_length=1, max_length=60)
    rate: TableRateIn
    sortOrder: Optional[int] = 0
    active: Optional[bool] = True  # web bhejta hai create pe

    @model_validator(mode="before")
    @classmethod
    def _flat_rate(cls, data):
        """Web flat body bhejta hai: {name, hourlyRate, ratesByPlayers, …, glovePrice}
        — nested `rate` me normalize kar do (Flutter nested bhejti hai)."""
        if isinstance(data, dict) and "rate" not in data and "hourlyRate" in data:
            data["rate"] = {k: data.get(k) for k in (
                "hourlyRate", "ratesByPlayers", "minCharge", "peakHourlyRate",
                "peakStartHour", "peakEndHour", "glovePrice")}
        return data


class TablePatchIn(In):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    rate: Optional[TableRateIn] = None
    sortOrder: Optional[int] = None
    active: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def _flat_rate(cls, data):
        """Web PATCH bhi flat bhejti hai (peakHourlyRate: null = peak off)."""
        if isinstance(data, dict) and "rate" not in data and "hourlyRate" in data:
            data["rate"] = {k: data.get(k) for k in (
                "hourlyRate", "ratesByPlayers", "minCharge", "peakHourlyRate",
                "peakStartHour", "peakEndHour", "glovePrice")}
        return data


# -------------------------------------------------------------------- members
class MemberIn(In):
    name: str = Field(min_length=1, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=20)   # web null bhej sakta hai
    email: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=400)  # kabhi required nahi
    walletBalance: float = Field(default=0, ge=0, le=10000000)  # opening wallet credit (migrated)
    dueAmount: float = Field(default=0, ge=0, le=10000000)      # opening due (migrated)
    passFramesLeft: int = Field(default=0, ge=0, le=10000)      # opening pass frames (web bhejta hai)
    planId: Optional[str] = None  # web Add Player — select karte hi plan benefits apply
    planPaid: bool = True  # join ke saath plan ka paisa book (day-close/monthly/finance sync)
    mode: Literal["cash", "upi", "card"] = "cash"  # plan payment ka mode
    planPaymentMode: Optional[Literal["cash", "upi", "card"]] = None  # web alias (wins over mode)


class MemberPatchIn(In):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=20)
    email: Optional[str] = Field(default=None, max_length=120)
    notes: Optional[str] = Field(default=None, max_length=400)
    active: Optional[bool] = None
    planExpiresAt: Optional[str] = None  # ISO date / datetime; admin override
    planId: Optional[str] = None  # DIFFERENT plan id aaya to turant sell+book (same id echo = no-op)
    mode: Optional[Literal["cash", "upi", "card"]] = None  # plan sell ka mode (default cash)
    planPaymentMode: Optional[Literal["cash", "upi", "card"]] = None  # web alias (wins over mode)
    # direct-set numerics (web "Set Balance" + Due Desk inline edits)
    walletBalance: Optional[float] = Field(default=None, ge=0, le=10000000)
    dueAmount: Optional[float] = Field(default=None, ge=0, le=10000000)
    passFramesLeft: Optional[int] = Field(default=None, ge=0, le=10000)
    # web aliases — koi bhi aaye walletBalance set samjho
    balance: Optional[float] = Field(default=None, ge=0, le=10000000)
    setBalance: Optional[float] = Field(default=None, ge=0, le=10000000)
    newBalance: Optional[float] = Field(default=None, ge=0, le=10000000)

    @model_validator(mode="after")
    def _balance_aliases(self):
        if self.walletBalance is None:
            for alt in (self.setBalance, self.balance, self.newBalance):
                if alt is not None:
                    self.walletBalance = alt
                    break
        return self


class MemberPaymentIn(In):
    amount: float = Field(gt=0, le=1000000)
    mode: Literal["cash", "upi", "card"] = "cash"


class NotifyIn(In):
    channel: Literal["email"] = "email"


# ---------------------------------------------------------------- plans (club membership plans)
class PlanIn(In):
    name: str = Field(min_length=1, max_length=80)
    type: Literal["wallet", "pass", "monthly"]
    amount: float = Field(ge=0, le=1000000)
    value: Optional[float] = Field(default=None, ge=0, le=10000000)  # wallet credit
    frames: Optional[int] = Field(default=None, ge=0, le=10000)      # pass frames
    days: Optional[int] = Field(default=None, ge=0, le=3650)         # pass/monthly validity
    tableDiscountPercent: Optional[float] = Field(default=None, ge=0, le=100)
    description: Optional[str] = Field(default=None, max_length=200)  # web null bhejta hai
    isDefault: bool = False

    @model_validator(mode="after")
    def _web_plan_shape(self):
        self.description = (self.description or "").strip()
        # web pass-plan frames `value` me bhejti hai — map to frames
        if self.type == "pass" and not self.frames and self.value:
            self.frames = int(self.value)
        return self


class PlanPatchIn(In):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    amount: Optional[float] = Field(default=None, ge=0, le=1000000)
    value: Optional[float] = Field(default=None, ge=0, le=10000000)
    frames: Optional[int] = Field(default=None, ge=0, le=10000)
    days: Optional[int] = Field(default=None, ge=0, le=3650)
    tableDiscountPercent: Optional[float] = Field(default=None, ge=0, le=100)
    description: Optional[str] = Field(default=None, max_length=200)
    isDefault: Optional[bool] = None
    active: Optional[bool] = None


class SellPlanIn(In):
    memberId: str
    mode: Literal["cash", "upi", "card"] = "cash"


class PlanPaymentIn(In):
    """Late/reconcile — join pe unpaid assign hua plan ka paisa ab book karo."""
    mode: Literal["cash", "upi", "card"] = "cash"


# ------------------------------------------------------------------- sessions
class SessionPlayerIn(In):
    label: str = Field(min_length=1, max_length=80)
    type: Literal["member", "guest"] = "guest"
    memberId: Optional[str] = None
    team: Optional[Literal["A", "B"]] = None


class SessionStartIn(In):
    tableId: str
    players: list[SessionPlayerIn] = Field(min_length=1, max_length=8)
    matchMode: Literal["solo", "2v2"] = "solo"
    advancePaid: float = Field(default=0, ge=0, le=100000)
    advanceMode: Literal["cash", "upi", "card"] = "cash"
    notes: str = Field(default="", max_length=300)
    gloveSeatIndexes: list[int] = Field(default_factory=list)


class SessionConfirmIn(In):
    winners: list[str] = Field(default_factory=list)     # player pids (solo)
    winningTeam: Optional[Literal["A", "B"]] = None      # 2v2
    discount: float = Field(default=0, ge=0, le=1000000)
    cashPaid: float = Field(default=0, ge=0, le=1000000)
    mode: Literal["cash", "upi", "card"] = "cash"
    usePass: list[str] = Field(default_factory=list)     # memberIds to bill via frame pass
    # ---- web-frontend aliases / extras (safe; classical keys untouched) ----
    items: list["BillItemIn"] = Field(default_factory=list)  # confirm-time attach
    winnerPlayerIds: Optional[list[str]] = None          # alias of winners
    paymentMode: Optional[str] = None                    # alias of mode (cash/upi/card)
    paidAmount: Optional[float] = Field(default=None, ge=0, le=1000000)  # alias of cashPaid

    @model_validator(mode="after")
    def _web_aliases(self) -> "SessionConfirmIn":
        if not self.winners and self.winnerPlayerIds:
            self.winners = list(self.winnerPlayerIds)
        if self.paidAmount is not None and self.cashPaid == 0:
            self.cashPaid = self.paidAmount
        if self.paymentMode:
            pm = self.paymentMode.strip().lower()
            if pm in ("cash", "upi", "card"):
                self.mode = pm  # type: ignore[assignment]
            elif pm == "due":
                self.cashPaid = 0.0  # poora due — wallet/pass server khud apply karta hai
            # wallet/mixed → server wallet-first→due auto; log mode default rehta hai
        return self


class SessionItemsIn(In):
    menuItemId: Optional[str] = None
    itemId: Optional[str] = None                        # web alias
    qty: int = Field(default=1, gt=0, le=500)
    items: list["BillItemIn"] = Field(default_factory=list)  # web batch form

    @model_validator(mode="after")
    def _need_line(self) -> "SessionItemsIn":
        if not self.items and not (self.menuItemId or self.itemId):
            raise ValueError("menuItemId is required (ya items[] bhejo)")
        return self

    def lines(self) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = [(i.menuItemId, i.qty) for i in self.items if i.menuItemId]
        mid = self.menuItemId or self.itemId
        if mid:
            out.append((mid, self.qty))
        return out


class SessionAdvanceIn(In):
    amount: float = Field(gt=0, le=100000)
    mode: Literal["cash", "upi", "card"] = "cash"


class SessionPatchIn(In):
    notes: str = Field(default="", max_length=300)


class SessionMoveIn(In):
    tableId: str


class GloveReturnIn(In):
    playerId: str
    returned: bool = True


# --------------------------------------------------------------------- frames
class WinnersPatchIn(In):
    winners: list[str] = Field(default_factory=list)
    winningTeam: Optional[Literal["A", "B"]] = None
    usePass: Optional[list[str]] = None
    note: str = Field(default="", max_length=200)
    winnerPlayerIds: Optional[list[str]] = None  # web alias

    @model_validator(mode="after")
    def _web_winner_alias(self):
        if not self.winners and self.winnerPlayerIds:
            self.winners = list(self.winnerPlayerIds)
        return self


# ---------------------------------------------------------------------- items
class MenuItemIn(In):
    name: str = Field(min_length=1, max_length=80)
    category: str = Field(default="Cafe", max_length=60)
    price: float = Field(gt=0, le=100000)
    costPrice: float = Field(default=0, ge=0, le=100000)
    stockQty: int = Field(default=0, ge=0, le=1000000)
    unit: str = Field(default="pc", max_length=20)
    reorderLevel: int = Field(default=5, ge=0, le=100000)


class MenuItemPatchIn(In):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    category: Optional[str] = Field(default=None, max_length=60)
    price: Optional[float] = Field(default=None, gt=0, le=100000)
    costPrice: Optional[float] = Field(default=None, ge=0, le=100000)
    unit: Optional[str] = Field(default=None, max_length=20)
    active: Optional[bool] = None
    reorderLevel: Optional[int] = Field(default=None, ge=0, le=100000)


class RestockIn(In):
    qty: int = Field(gt=0, le=1000000)
    unitCost: float = Field(ge=0, le=100000)


class BillItemIn(In):
    # `menuItemId` canonical hai; web app ka purana `itemId` bhi chal jayega.
    menuItemId: Optional[str] = None
    itemId: Optional[str] = None
    qty: int = Field(gt=0, le=500)

    @model_validator(mode="after")
    def _alias_item_id(self) -> "BillItemIn":
        if self.menuItemId is None and self.itemId is not None:
            self.menuItemId = self.itemId
        if not self.menuItemId:
            raise ValueError("menuItemId is required for each bill item")
        return self


class MixedPaymentIn(In):
    mode: Literal["cash", "upi", "card", "wallet"]
    amount: float = Field(gt=0, le=1000000)


class ItemBillIn(In):
    items: list[BillItemIn] = Field(min_length=1, max_length=50)
    customerName: Optional[str] = Field(default=None, max_length=80)  # falls back to member name
    memberId: Optional[str] = None
    mode: Literal["cash", "upi", "card", "wallet", "due", "mixed"] = "cash"
    payments: list[MixedPaymentIn] = Field(default_factory=list)      # for mixed
    discount: float = Field(default=0, ge=0, le=100000)


class ItemBillPatchIn(In):
    customerName: Optional[str] = Field(default=None, max_length=80)


class MarkPaidIn(In):
    mode: Literal["cash", "upi", "card"] = "cash"
    amount: Optional[float] = Field(default=None, gt=0, le=1000000)  # web partial pay; None = full outstanding


# ------------------------------------------------------------------- expenses
class ExpenseIn(In):
    title: str = Field(min_length=1, max_length=120)
    category: Literal["rent", "salary", "electricity", "maintenance", "stock",
                      "tournament", "misc"]
    amount: float = Field(gt=0, le=10000000)
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")  # None → today
    note: Optional[str] = Field(default=None, max_length=300)  # web sends null sometimes


# --------------------------------------------------------------- tournaments
class TournamentIn(In):
    name: str = Field(min_length=2, max_length=100)
    game: str = Field(default="Snooker", max_length=40)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    entryFee: float = Field(default=0, ge=0, le=1000000)
    prize1: float = Field(default=0, ge=0, le=10000000)
    prize2: float = Field(default=0, ge=0, le=10000000)
    maxPlayers: int = Field(default=16, ge=2, le=64)
    tableRate: float = Field(default=0, ge=0, le=100000)  # 0 = use table rate+minCharge
    format: Literal["knockout", "league"] = "knockout"
    notes: Optional[str] = Field(default=None, max_length=400)  # web null bhej sakta hai


class TournamentPatchIn(In):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    game: Optional[str] = Field(default=None, max_length=40)
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    entryFee: Optional[float] = Field(default=None, ge=0, le=1000000)
    prize1: Optional[float] = Field(default=None, ge=0, le=10000000)
    prize2: Optional[float] = Field(default=None, ge=0, le=10000000)
    maxPlayers: Optional[int] = Field(default=None, ge=2, le=64)
    tableRate: Optional[float] = Field(default=None, ge=0, le=100000)
    notes: Optional[str] = Field(default=None, max_length=400)
    status: Optional[Literal["upcoming", "cancelled"]] = None  # format locked after create


class ParticipantIn(In):
    name: str = Field(min_length=1, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=20)  # optional — kabhi force nahi
    memberId: Optional[str] = None
    paidEntry: bool = False                        # web: paid now at add-time
    mode: Literal["cash", "upi", "card"] = "cash"  # payment mode for that fee

    @model_validator(mode="after")
    def _norm_phone(self):
        self.phone = (self.phone or "").strip()
        return self


class ParticipantPatchIn(In):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    phone: Optional[str] = Field(default=None, max_length=20)
    paidEntry: Optional[bool] = None
    mode: Optional[Literal["cash", "upi", "card"]] = None  # web mark-paid mode


class PlayIn(In):
    tableId: Optional[str] = None  # null = tracker off (score-only flow); route 400s during play


class ResultIn(In):
    score1: int = Field(ge=0, le=200)
    score2: int = Field(ge=0, le=200)
    winnerPid: Optional[str] = None
    winner: Optional[Literal["1", "2"]] = None  # web alias: slot number → route resolves pid
    mode: Literal["cash", "upi", "card"] = "cash"


# ------------------------------------------------------------------- master
class MasterPlanIn(In):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=200)
    price: float = Field(ge=0, le=10000000)
    billingCycle: Literal["monthly", "yearly"] = "monthly"
    durationDays: int = Field(default=30, ge=1, le=3650)
    trialDays: int = Field(default=0, ge=0, le=365)
    maxClubs: int = Field(default=1, ge=1, le=100)
    features: list[str] = Field(default_factory=list)
    recommended: bool = False
    sortOrder: int = 0


class MasterPlanPatchIn(In):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=200)
    price: Optional[float] = Field(default=None, ge=0, le=10000000)
    billingCycle: Optional[Literal["monthly", "yearly"]] = None
    durationDays: Optional[int] = Field(default=None, ge=1, le=3650)
    trialDays: Optional[int] = Field(default=None, ge=0, le=365)
    maxClubs: Optional[int] = Field(default=None, ge=1, le=100)
    features: Optional[list[str]] = None
    recommended: Optional[bool] = None
    sortOrder: Optional[int] = None
    active: Optional[bool] = None


class MasterUserPatchIn(In):
    role: Optional[Literal["owner", "staff"]] = None
    clubIds: Optional[list[str]] = None
    active: Optional[bool] = None
    phone: Optional[str] = Field(default=None, max_length=20)
    location: Optional[str] = Field(default=None, max_length=120)


class MasterSubPatchIn(In):
    planId: Optional[str] = None
    planName: Optional[str] = Field(default=None, max_length=80)
    status: Optional[Literal["pending", "trial", "active", "past_due", "paused",
                             "expired", "cancelled"]] = None
    price: Optional[float] = Field(default=None, ge=0, le=10000000)
    durationDays: Optional[int] = Field(default=None, ge=1, le=3650)
    maxClubs: Optional[int] = Field(default=None, ge=1, le=100)
    startsAt: Optional[str] = None
    expiresAt: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=300)


class SelectPlanIn(In):
    planId: str


# ------------------------------------------------------------------ platform
class SupportPatchIn(In):
    email: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=24)
