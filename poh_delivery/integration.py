"""Точка подключения к Harness — единственное, что импортирует чужой код.

Harness на старте воркера вызывает `install(...)`, отдавая свою функцию выдачи
токена, и регистрирует `WORKFLOWS` + `ACTIVITIES` на очереди `TASK_QUEUE`.
Больше он о Delivery-Agent не знает ничего: ни про GitHub-клиент, ни про docker,
ни про правила очереди.

Обратная зависимость ровно одна и объявлена явно: активность
`delivery_fix_conflicts` на очереди Harness — это агент разработки, который
живёт там и вызывается по имени.
"""

from poh_delivery import activities as _activities
from poh_delivery import ports
from poh_delivery.github import GitHubApi, env_token_provider
from poh_delivery.prod import DockerProd
from poh_delivery.workflow import DeliveryRelease

TASK_QUEUE = "delivery"
WORKFLOWS = [DeliveryRelease]
ACTIVITIES = _activities.ALL

# Имя активности, которую Delivery-Agent ждёт от Harness. Объявлено здесь,
# чтобы сторона Harness регистрировала её под тем же именем, а не по памяти.
CONFLICT_FIX_ACTIVITY = "delivery_fix_conflicts"


def install(token_provider=env_token_provider, dry_run: bool = False) -> None:
    ports.configure(
        github=GitHubApi(token_provider, dry_run=dry_run),
        prod=DockerProd(token_provider, dry_run=dry_run),
    )
