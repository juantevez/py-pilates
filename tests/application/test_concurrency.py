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


def test_concurrent_admin_and_student_bookings_never_exceed_capacity(container):
    container.generate_daily_classes.execute(fecha=date(2026, 10, 13))
    clase = container.list_classes_for_date.execute(fecha=date(2026, 10, 13))[0]
    admin = container.register_user.execute(email="admin@test.com", full_name="Admin", password="secreta123", role=Role.ADMIN)
    users = [
        container.register_user.execute(email=f"m{i}@test.com", full_name=f"M{i}", password="secreta123", role=Role.ALUMNO)
        for i in range(10)
    ]

    def attempt(args):
        i, user_id = args
        try:
            if i % 2:
                container.admin_book_class.execute(admin_id=admin.id, user_id=user_id, clase_id=clase.id)
            else:
                container.book_class.execute(user_id=user_id, clase_id=clase.id)
            return "ok"
        except ConflictError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, enumerate(u.id for u in users)))

    assert results.count("ok") == 4
    assert results.count("class_full") == 6
    assert container.list_classes_for_date.execute(fecha=date(2026, 10, 13))[0].reservados == 4


def test_waitlist_promotion_racing_direct_bookings_never_exceeds_capacity(container):
    container.generate_daily_classes.execute(fecha=date(2026, 10, 13))
    clase = container.list_classes_for_date.execute(fecha=date(2026, 10, 13))[0]
    user = lambda i: container.register_user.execute(  # noqa: E731
        email=f"w{i}@test.com", full_name=f"W{i}", password="secreta123", role=Role.ALUMNO
    )
    holders = [user(i) for i in range(4)]
    turnos = [container.book_class.execute(user_id=u.id, clase_id=clase.id) for u in holders]
    waiting = user(99)
    container.join_waitlist.execute(user_id=waiting.id, clase_id=clase.id)
    racers = [user(i) for i in range(10, 16)]

    def cancel():
        container.cancel_booking.execute(user_id=holders[0].id, turno_id=turnos[0].id)
        return "cancel"

    def attempt(user_id):
        try:
            container.book_class.execute(user_id=user_id, clase_id=clase.id)
            return "ok"
        except ConflictError as e:
            return e.code

    with ThreadPoolExecutor(max_workers=7) as pool:
        futures = [pool.submit(cancel)] + [pool.submit(attempt, r.id) for r in racers]
        results = [f.result() for f in futures]

    assert results.count("ok") <= 1  # el lugar liberado lo toma un solo alumno (o el de la lista)
    assert container.list_classes_for_date.execute(fecha=date(2026, 10, 13))[0].reservados == 4
