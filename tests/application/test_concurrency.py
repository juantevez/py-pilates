from concurrent.futures import ThreadPoolExecutor
from datetime import date

from pilates.identity.domain.model import Role
from pilates.shared.domain.errors import ConflictError


def test_concurrent_bookings_never_exceed_capacity(container):
    container.generate_daily_classes.execute(fecha=date(2026, 10, 13))
    clase = container.list_classes_for_date.execute(fecha=date(2026, 10, 13))[0]
    users = [
        container.register_user.execute(email=f"c{i}@test.com", full_name=f"C{i}", password="secreta123", role=Role.ALUMNO)
        for i in range(10)
    ]

    def attempt(user_id):
        try:
            container.book_class.execute(user_id=user_id, clase_id=clase.id)
            return "ok"
        except ConflictError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, [u.id for u in users]))

    assert results.count("ok") == 4
    assert results.count("class_full") == 6
    assert container.list_classes_for_date.execute(fecha=date(2026, 10, 13))[0].reservados == 4
