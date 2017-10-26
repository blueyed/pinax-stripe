import decimal

from django.conf import settings
from django.db.models import Q

import stripe

from .. import hooks, models, utils


def calculate_refund_amount(charge, amount=None):
    """
    Calculate refund amount given a charge and optional amount.

    Args:
        charge: a pinax.stripe.models.Charge object
        amount: optionally, the decimal.Decimal amount you wish to refund
    """
    eligible_to_refund = charge.amount - (charge.amount_refunded or 0)
    if amount:
        return min(eligible_to_refund, amount)
    return eligible_to_refund


def capture(charge, amount=None):
    """
    Capture the payment of an existing, uncaptured, charge.

    Args:
        charge: a pinax.stripe.models.Charge object
        amount: the decimal.Decimal amount of the charge to capture
    """
    stripe_charge = charge.stripe_charge.capture(
        amount=utils.convert_amount_for_api(
            amount if amount else charge.amount,
            charge.currency
        ),
        expand=["balance_transaction"]
    )
    sync_charge_from_stripe_data(stripe_charge)


def _validate_create_params(amount, application_fee, destination_account, destination_amount, on_behalf_of):
    if not isinstance(amount, decimal.Decimal):
        raise ValueError(
            "You must supply a decimal value for `amount`."
        )
    if application_fee and not isinstance(application_fee, decimal.Decimal):
        raise ValueError(
            "You must supply a decimal value for `application_fee`."
        )
    if application_fee and not destination_account:
        raise ValueError(
            "You can only specify `application_fee` with `destination_account`"
        )
    if application_fee and destination_account and destination_amount:
        raise ValueError(
            "You can't specify `application_fee` with `destination_amount`"
        )
    if destination_account and on_behalf_of:
        raise ValueError(
            "`destination_account` and `on_behalf_of` are mutualy exclusive")


def create(
    amount, customer, source=None, currency="usd", description=None,
    send_receipt=settings.PINAX_STRIPE_SEND_EMAIL_RECEIPTS, capture=True,
    email=None, destination_account=None, destination_amount=None,
    application_fee=None, on_behalf_of=None,
):
    """
    Create a charge for the given customer.

    Args:
        amount: should be a decimal.Decimal amount
        customer: the Stripe id of the customer to charge
        source: the Stripe id of the source belonging to the customer
        currency: the currency with which to charge the amount in
        description: a description of the charge
        send_receipt: send a receipt upon successful charge
        capture: immediately capture the charge instead of doing a pre-authorization
        destination_account: stripe_id of a connected account
        destination_amount: amount to transfer to the `destination_account` without creating an application fee
        application_fee: used with `destination_account` to add a fee destined for the platform account
        on_behalf_of: stripe_id of a connected account. Creates direct Charges to this account.

    Returns:
        a pinax.stripe.models.Charge object
    """
    _validate_create_params(amount, application_fee, destination_account, destination_amount, on_behalf_of)
    kwargs = dict(
        amount=utils.convert_amount_for_api(amount, currency),  # find the final amount
        currency=currency,
        source=source,
        customer=customer,
        description=description,
        capture=capture,
    )
    if destination_account:
        kwargs["destination"] = {"account": destination_account}
        if destination_amount:
            kwargs["destination"]["amount"] = utils.convert_amount_for_api(
                destination_amount,
                currency
            )
        if application_fee:
            kwargs["application_fee"] = utils.convert_amount_for_api(
                application_fee, currency
            )
    elif on_behalf_of:
        kwargs["on_behalf_of"] = on_behalf_of
    stripe_charge = stripe.Charge.create(
        **kwargs
    )
    charge = sync_charge_from_stripe_data(stripe_charge)
    if send_receipt:
        hooks.hookset.send_receipt(charge, email)
    return charge


def retrieve(stripe_id, stripe_account=None):
    """Retrieve a Charge plus its balance info."""
    return stripe.Charge.retrieve(
        stripe_id,
        stripe_account=stripe_account,
        expand=["balance_transaction"]
    )


def sync_charges_for_customer(customer):
    """
    Populate database with all the charges for a customer.

    Args:
        customer: a pinax.stripe.models.Customer object
    """
    for charge in customer.stripe_customer.charges().data:
        sync_charge_from_stripe_data(charge)


def sync_charge(stripe_id, stripe_account=None):
    """Sync a charge given a Stripe charge ID."""
    return sync_charge_from_stripe_data(
        retrieve(stripe_id, stripe_account=stripe_account)
    )


def sync_charge_from_stripe_data(data):
    """
    Create or update the charge represented by the data from a Stripe API query.

    Args:
        data: the data representing a charge object in the Stripe API

    Returns:
        a pinax.stripe.models.Charge object
    """
    obj, _ = models.Charge.objects.get_or_create(stripe_id=data["id"])
    obj.customer = models.Customer.objects.filter(stripe_id=data["customer"]).first()
    obj.source = data["source"]["id"]
    obj.currency = data["currency"]
    obj.invoice = models.Invoice.objects.filter(stripe_id=data["invoice"]).first()
    obj.amount = utils.convert_amount_for_db(data["amount"], obj.currency)
    obj.paid = data["paid"]
    obj.refunded = data["refunded"]
    obj.captured = data["captured"]
    obj.disputed = data["dispute"] is not None
    obj.charge_created = utils.convert_tstamp(data, "created")
    if data.get("description"):
        obj.description = data["description"]
    if data.get("amount_refunded"):
        obj.amount_refunded = utils.convert_amount_for_db(data["amount_refunded"], obj.currency)
    if data["refunded"]:
        obj.amount_refunded = obj.amount
    balance_transaction = data.get("balance_transaction")
    if balance_transaction and not isinstance(balance_transaction, str):
        obj.available = balance_transaction["status"] == "available"
        obj.available_on = utils.convert_tstamp(
            balance_transaction, "available_on"
        )
        obj.fee = utils.convert_amount_for_db(
            balance_transaction["fee"], balance_transaction["currency"]
        )
        obj.fee_currency = balance_transaction["currency"]
    obj.transfer_group = data.get("transfer_group")
    obj.save()
    return obj


def update_charge_availability():
    """
    Update `available` and `available_on` attributes of Charges.

    We only bother checking those Charges that can become available.
    """
    charges = models.Charge.objects.filter(
        paid=True,
        captured=True
    ).exclude(
        Q(available=True) | Q(refunded=True)
    ).select_related(
        "customer"
    )
    for c in charges.iterator():
        sync_charge(
            c.stripe_id,
            stripe_account=c.customer.stripe_account
        )
