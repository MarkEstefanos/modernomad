import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views.generic import View

from bank import forms, models

logger = logging.getLogger(__name__)


def create_transaction(reason, amount, from_account, to_account):
    t = models.Transaction.objects.create(reason=reason)
    e1 = models.Entry.objects.create(
        account=from_account, amount=(amount) * (-1), transaction=t
    )
    e2 = models.Entry.objects.create(account=to_account, amount=(amount), transaction=t)

    try:
        assert t.valid
        return True
    except Exception:
        logger.error(
            "transaction was invalid. please check transaction ids %d and %d"
            % (e1.id, e2.id)
        )
        return False


# Create your views here.
class AccountDetail(View):
    template_name = "accounts_detail.html"

    def get(self, request, account_id):
        try:
            account = models.Account.objects.get(id=account_id)
            # ensure user has permissions
            assert (
                request.user in account.owners.all()
                or request.user in account.admins.all()
            )
            return render(request, self.template_name, {"account": account})
        except Exception:
            messages.info(
                request,
                "The account does not exist or you are not authorized.",
            )
            return HttpResponseRedirect("/404")


class AccountList(View):
    template_name = "accounts_list.html"
    form_class = forms.TransactionForm

    @method_decorator(login_required)
    def get(self, request):
        accounts = models.Account.objects.filter(owners=request.user).order_by(
            "currency"
        )
        transaction_form = self.form_class(request.user)
        return render(
            request,
            self.template_name,
            {"accounts": accounts, "transaction_form": transaction_form},
        )

    @method_decorator(login_required)
    def post(self, request):
        transaction_form = self.form_class(request.user, request.POST)
        if transaction_form.is_valid():
            to_account_id = request.POST.get("to_account")
            from_account_id = request.POST.get("from_account")
            to_account = models.Account.objects.get(id=to_account_id)
            from_account = models.Account.objects.get(id=from_account_id)
            amount = int(request.POST.get("amount"))
            reason = request.POST.get("reason")

            prerequisites_met = True
            # check if user has permissions
            try:
                assert (
                    request.user in from_account.owners.all()
                    or request.user in from_account.admins.all()
                )
            except Exception:
                messages.info(
                    request,
                    "You do not have permission to transfer from this account",
                )
                prerequisites_met = False

            # check if balances are valid
            try:
                assert from_account.get_balance() >= amount
            except AssertionError:
                messages.info(
                    request,
                    "Insufficient balance on source account %s (%d)"
                    % (from_account.name, from_account.id),
                )
                prerequisites_met = False

            # ensure not to and from same account
            try:
                assert to_account != from_account
            except Exception:
                messages.info(request, "You must select two different accounts")
                prerequisites_met = False

            if prerequisites_met:
                success = create_transaction(reason, amount, from_account, to_account)
                if success:
                    messages.info(request, "Submitted")
                else:
                    messages.info(
                        request,
                        "Oops, something went wrong. Please try again.",
                    )
        else:
            logger.debug(transaction_form.errors)
            messages.info(
                request,
                f"The form contained errors, your transfer was not completed. {transaction_form.errors}",
            )

        return redirect("account_list")
