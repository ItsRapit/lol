from __future__ import annotations
from dataclasses import dataclass
from app.db import Database


@dataclass
class PaymentInstructions:
    title: str
    text: str


class PaymentProvider:
    code = "base"

    async def instructions(self, db: Database, tx) -> PaymentInstructions:
        raise NotImplementedError


class CardToCardPaymentProvider(PaymentProvider):
    code = "card_to_card"

    async def instructions(self, db: Database, tx) -> PaymentInstructions:
        card = await db.get_setting("payment_card_number", "تنظیم نشده")
        final_price = tx["final_price_label"] or tx["price_label"]
        return PaymentInstructions(
            title="پرداخت کارت‌به‌کارت",
            text=(
                f"بسته: {tx['title']}\n"
                f"مبلغ نهایی: <b>{final_price}</b>\n"
                f"شماره کارت:\n<code>{card}</code>\n\n"
                "بعد از واریز، رسید را به‌صورت عکس یا متن همین‌جا ارسال کن."
            ),
        )


PROVIDERS = {
    CardToCardPaymentProvider.code: CardToCardPaymentProvider(),
}


async def get_payment_provider(db: Database) -> PaymentProvider:
    code = await db.get_setting("payment_method", "card_to_card")
    return PROVIDERS.get(code, PROVIDERS["card_to_card"])
