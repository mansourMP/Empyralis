from server_modules import sage_agent_computer_selection_service as service


def test_set_get_selection_by_workspace_and_user(tmp_path):
    db_path = tmp_path / "selection.sqlite3"

    selection = service.set_selection(
        workspace_id="ws-1",
        user_id="user-1",
        selected_gateway_id="gateway-1",
        selected_by="user-1",
        metadata={"device_name": "Mac mini"},
        db_path=db_path,
    )

    assert selection["workspace_id"] == "ws-1"
    assert selection["user_id"] == "user-1"
    assert selection["selected_gateway_id"] == "gateway-1"
    assert selection["selected_by"] == "user-1"
    assert selection["metadata"] == {"device_name": "Mac mini"}

    persisted = service.get_selection(workspace_id="ws-1", user_id="user-1", db_path=db_path)
    assert persisted == selection


def test_second_user_cannot_read_first_users_selection(tmp_path):
    db_path = tmp_path / "selection.sqlite3"
    service.set_selection(
        workspace_id="ws-1",
        user_id="user-1",
        selected_gateway_id="gateway-1",
        selected_by="user-1",
        db_path=db_path,
    )

    assert service.get_selection(workspace_id="ws-1", user_id="user-2", db_path=db_path) is None


def test_updating_selection_replaces_old_gateway(tmp_path):
    db_path = tmp_path / "selection.sqlite3"
    service.set_selection(
        workspace_id="ws-1",
        user_id="user-1",
        selected_gateway_id="gateway-1",
        selected_by="user-1",
        db_path=db_path,
    )

    updated = service.set_selection(
        workspace_id="ws-1",
        user_id="user-1",
        selected_gateway_id="gateway-2",
        selected_by="user-1",
        db_path=db_path,
    )

    assert updated["selected_gateway_id"] == "gateway-2"
    assert service.get_selection(workspace_id="ws-1", user_id="user-1", db_path=db_path)[
        "selected_gateway_id"
    ] == "gateway-2"
