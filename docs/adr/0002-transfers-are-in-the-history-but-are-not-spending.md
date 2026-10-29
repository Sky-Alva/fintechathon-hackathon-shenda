# Transfers are in the transaction history, but are not spending

Every transfer the Bank completes (immediate, a Scheduled Transfer when it runs, a held one when it is sent) is written to the transaction history: a debit for the sender, with the Payee's name as the counterparty (`merchant`) and the transfer's note as the `description`, and a credit for the receiving Customer when the Payee's account is in the Bank. Both rows use the `transfer` category. A refused transfer, or a Scheduled Transfer that has not run, leaves no row. The seeded past transfers are in the history too.

The `transfer` category does not count as spending or as income: spending summaries, monthly and annual reports leave it out of every total and series, and the Unusual Transaction and Recurring Charge rules ignore it. Moving money to a spouse or a parent is not something the Customer bought, and counting it would double-count household money between 李明 and 韩梅梅 and make every first transfer to a new Payee look unusual. We leave transfers out of income too, so that a transfer between two Customers never shows as income for one without being spending for the other.

A paid Merchant order is a purchase, not a transfer: it is written as a `gifts` debit, with the Merchant as the counterparty and the order's note (or else the product) as the description, and counts as spending like the seeded flower purchase does.

## Considered Options

- **Count transfers as spending:** the Customer's outflow would match their spending total, but household transfers would swamp every report and trip the Unusual Transaction rules on any new Payee.
- **Keep transfers out of the history (a separate "list transfers" Operation):** reports stay as they are, but "find my transfer with the remark 家用" needs a second Operation, and the Customer's history is no longer the whole picture of their account.
