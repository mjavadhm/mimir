from peyda_pm_bot.application.ports import UowFactory


class PersistentLimits:
    def __init__(self, uow: UowFactory) -> None:
        self.uow = uow

    async def get(self) -> tuple[float, float]:
        async with self.uow() as unit:
            return await unit.github_limits.get()

    async def save(self, not_before: float, next_write: float) -> None:
        async with self.uow() as unit:
            await unit.github_limits.save(not_before, next_write)
