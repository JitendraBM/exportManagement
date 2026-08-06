"""
Tests for TransporterService (app/services.py) and its routes.

A transporter is the one party type with no originating lead and no status
pipeline, so what's worth pinning down is: the admin-only write gate, the
company scoping (another tenant's transporter must 404, not 403), the
contact rows being rewritten wholesale on every save, and the delete taking
its contacts with it.
"""

import pytest

from app.exceptions import ValidationError, PermissionDeniedError, NotFoundError


FIELDS = {
    "name": "Blue Line Logistics",
    "address": "Plot 4, Transport Nagar, Morbi",
    "gstin_transporter_no": "24AAACB1234C1ZQ",
    "pan_no": "AAACB1234C",
    "cin_llp_no": "U60231GJ2011PTC065432",
    "email": "ops@blueline.example",
}


def _contacts(*names, primary_index=None):
    return [
        {"name": n, "phone": f"90000000{i}", "email": f"{n}@blueline.example",
         "is_primary": (i == primary_index)}
        for i, n in enumerate(names)
    ]


class TestCreateTransporter:
    def test_admin_can_create_with_every_field(self, container, seed):
        t = container.transporter_service.create(seed.admin, dict(FIELDS), _contacts("raj", primary_index=0))

        assert t.id is not None
        assert t.company_id == seed.company_id
        for field, value in FIELDS.items():
            assert getattr(t, field) == value
        assert [c.name for c in t.contacts] == ["raj"]
        assert t.contacts[0].is_primary

    def test_employee_cannot_create(self, container, seed):
        with pytest.raises(PermissionDeniedError):
            container.transporter_service.create(seed.employee, dict(FIELDS), [])

    def test_name_is_compulsory(self, container, seed):
        with pytest.raises(ValidationError):
            container.transporter_service.create(seed.admin, {**FIELDS, "name": "   "}, [])

    def test_blank_optional_fields_are_stored_as_null_not_empty_string(self, container, seed):
        t = container.transporter_service.create(seed.admin, {"name": "Solo Carriers"}, [])

        assert t.address is None and t.gstin_transporter_no is None
        assert t.pan_no is None and t.cin_llp_no is None and t.email is None

    def test_blank_contact_rows_the_form_always_submits_are_dropped(self, container, seed):
        rows = [{"name": "  ", "phone": "", "email": "", "is_primary": True},
                {"name": "Meera", "phone": "555", "email": "", "is_primary": False}]
        t = container.transporter_service.create(seed.admin, dict(FIELDS), rows)

        assert [c.name for c in t.contacts] == ["Meera"]
        # Nothing was marked primary, so the first surviving row becomes it.
        assert t.contacts[0].is_primary


class TestReadTransporter:
    def test_another_companys_transporter_is_a_404(self, container, seed):
        t = container.transporter_service.create(seed.admin, dict(FIELDS), [])
        other_tenant = container.tenant_repo.create("Rival Exports", "rival-exports")

        with pytest.raises(NotFoundError):
            container.transporter_service.get(t.id, other_tenant.id)

    def test_list_is_company_scoped_and_name_ordered(self, container, seed):
        container.transporter_service.create(seed.admin, {"name": "Zenith Roadways"}, [])
        container.transporter_service.create(seed.admin, {"name": "abbot Cargo"}, [])
        other_tenant = container.tenant_repo.create("Rival Exports", "rival-exports")
        other_admin = container.auth_service.create_user(
            company_id=other_tenant.id, username="rival", password="rival-pass-123",
            full_name="Rival Admin", role="admin",
        )
        container.transporter_service.create(other_admin, {"name": "Not Ours"}, [])

        names = [t.name for t in container.transporter_service.list_all(seed.company_id)]
        assert names == ["abbot Cargo", "Zenith Roadways"]


class TestUpdateTransporter:
    def test_admin_edit_replaces_fields_and_the_whole_contact_set(self, container, seed):
        t = container.transporter_service.create(seed.admin, dict(FIELDS), _contacts("raj", "meera", primary_index=0))

        updated = container.transporter_service.update(
            t.id, seed.admin, {**FIELDS, "name": "Blue Line Logistics Pvt Ltd", "email": ""},
            _contacts("sunil", primary_index=0),
        )

        assert updated.name == "Blue Line Logistics Pvt Ltd"
        assert updated.email is None
        assert [c.name for c in updated.contacts] == ["sunil"]

    def test_employee_cannot_edit(self, container, seed):
        t = container.transporter_service.create(seed.admin, dict(FIELDS), [])
        with pytest.raises(PermissionDeniedError):
            container.transporter_service.update(t.id, seed.employee, dict(FIELDS), [])


class TestDeleteTransporter:
    def test_admin_delete_takes_the_contacts_with_it(self, container, seed, db):
        t = container.transporter_service.create(seed.admin, dict(FIELDS), _contacts("raj", primary_index=0))

        container.transporter_service.delete(t.id, seed.admin)

        with pytest.raises(NotFoundError):
            container.transporter_service.get(t.id, seed.company_id)
        left = db.query("SELECT * FROM transporter_contacts WHERE transporter_id = ?", (t.id,))
        assert left == []

    def test_employee_cannot_delete(self, container, seed):
        t = container.transporter_service.create(seed.admin, dict(FIELDS), [])
        with pytest.raises(PermissionDeniedError):
            container.transporter_service.delete(t.id, seed.employee)


class TestTransporterRoutes:
    def test_create_edit_and_view_round_trip(self, logged_in_admin):
        client, admin, company_id = logged_in_admin

        resp = client.post("/transporters/new", data={
            **FIELDS,
            "contact_name[]": ["Raj Patel", ""],
            "contact_phone[]": ["9000000001", ""],
            "contact_email[]": ["raj@blueline.example", ""],
            "primary_contact_index": "0",
        }, follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Blue Line Logistics" in body
        assert "24AAACB1234C1ZQ" in body
        assert "Raj Patel" in body

        transporter = client.application.container.transporter_service.list_all(company_id)[0]
        resp = client.post(f"/transporters/{transporter.id}/edit", data={
            **FIELDS, "name": "Blue Line Logistics Pvt Ltd",
            "contact_name[]": ["Meera S"], "contact_phone[]": ["9000000002"],
            "contact_email[]": [""], "primary_contact_index": "0",
        }, follow_redirects=True)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "Blue Line Logistics Pvt Ltd" in body
        assert "Meera S" in body and "Raj Patel" not in body

        assert "Blue Line Logistics Pvt Ltd" in client.get("/transporters/").get_data(as_text=True)

    def test_a_missing_name_re_renders_the_form_instead_of_saving(self, logged_in_admin):
        client, admin, company_id = logged_in_admin
        resp = client.post("/transporters/new", data={**FIELDS, "name": ""})

        assert resp.status_code == 400
        assert client.application.container.transporter_service.list_all(company_id) == []

    def test_delete_needs_the_right_password(self, logged_in_admin):
        client, admin, company_id = logged_in_admin
        service = client.application.container.transporter_service
        t = service.create(admin, dict(FIELDS), [])

        client.post(f"/transporters/{t.id}/delete", data={"delete_password": "nope"}, follow_redirects=True)
        assert service.list_all(company_id) != []

        client.post(f"/transporters/{t.id}/delete", data={"delete_password": "web-pass-123"}, follow_redirects=True)
        assert service.list_all(company_id) == []

    def test_an_employee_is_kept_out_of_the_write_routes(self, app, client):
        container = app.container
        tenant = container.tenant_repo.create("Web Co 2", "web-co-2")
        employee = container.auth_service.create_user(
            company_id=tenant.id, username="webemp", password="emp-pass-123",
            full_name="Web Employee", role="employee",
        )
        with client.session_transaction() as sess:
            sess["user_id"] = employee.id

        assert client.get("/transporters/").status_code == 200
        assert client.get("/transporters/new").status_code in (302, 403)
