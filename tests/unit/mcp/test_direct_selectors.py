from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from artemis.mcp.actuators import MockActuator


@pytest.mark.asyncio
async def test_selector_click_uses_live_bounds_and_resolution():
    from artemis.mcp.direct_selectors import DirectSelectors, Selector
    actuator = MockActuator(width=720, height=1280)
    result = await DirectSelectors(actuator).click(Selector(text="Settings"))
    assert result.ok
    assert actuator.action_history[-1]["x"] == 360
    assert abs(actuator.action_history[-1]["y"] - 50) <= 1


@pytest.mark.asyncio
async def test_ambiguous_selector_never_uses_fallback():
    from artemis.mcp.direct_selectors import DirectSelectors, Selector
    actuator = MockActuator()
    screen = await actuator.driver.get_screen_data()
    screen.ui_elements *= 2
    actuator.driver.get_screen_data = AsyncMock(return_value=screen)
    result = await DirectSelectors(actuator).click(Selector(text="Settings"), [500, 500])
    assert not result.ok
    assert "ambiguous" in result.message.lower()
    assert actuator.action_history == []


@pytest.mark.asyncio
async def test_missing_selector_uses_only_explicit_fallback():
    from artemis.mcp.direct_selectors import DirectSelectors, Selector
    actuator = MockActuator()
    selector = DirectSelectors(actuator)
    assert not (await selector.click(Selector(text="missing"))).ok
    assert not actuator.action_history
    result = await selector.click(Selector(text="missing"), [100, 200])
    assert result.ok
    assert "fallback" in result.message.lower()
    assert actuator.action_history[-1]["x"] == 108


@pytest.mark.asyncio
async def test_malformed_hierarchy_is_not_absence_and_never_falls_back():
    from artemis.mcp.direct_selectors import DirectSelectors, Selector
    actuator = MockActuator()
    screen = await actuator.driver.get_screen_data()
    screen.ui_hierarchy_xml = "<broken"
    screen.ui_elements = []
    actuator.driver.get_screen_data = AsyncMock(return_value=screen)
    selectors = DirectSelectors(actuator)
    assert not (await selectors.click(Selector(text="missing"), [100, 200])).ok
    assert not (await selectors.wait(Selector(text="missing"), "absent", 100)).ok
    assert not actuator.action_history


@pytest.mark.asyncio
async def test_missing_hierarchy_is_not_a_verified_empty_screen():
    from artemis.mcp.direct_selectors import DirectSelectors, Selector
    actuator = MockActuator()
    screen = await actuator.driver.get_screen_data()
    screen.ui_hierarchy_xml = None
    screen.ui_elements = []
    actuator.driver.get_screen_data = AsyncMock(return_value=screen)
    selectors = DirectSelectors(actuator)
    assert not (await selectors.click(Selector(text="missing"), [100, 200])).ok
    assert not (await selectors.wait(Selector(text="missing"), "absent", 100)).ok
    assert not actuator.action_history


@pytest.mark.asyncio
async def test_wait_times_out_and_app_wait_checks_real_foreground():
    from artemis.mcp.direct_selectors import DirectSelectors, Selector
    selectors = DirectSelectors(MockActuator())
    result = await selectors.wait(Selector(text="missing"), timeout_ms=50)
    assert result.code == "TIMEOUT"
    assert (await selectors.wait_app("com.android.settings", timeout_ms=50)).ok
    assert not (await selectors.wait_app("com.ronghui.ryb", timeout_ms=50)).ok


def test_empty_selector_rejected():
    from artemis.mcp.direct_selectors import Selector
    with pytest.raises(ValidationError):
        Selector()


def test_android_parser_preserves_selector_state():
    from artemis.drivers.android.hierarchy import parse_ui_hierarchy
    nodes = parse_ui_hierarchy(
        '<hierarchy><node text="" content-desc="Login" resource-id="app:id/login" '
        'enabled="false" package="com.example.app" bounds="[0,0][100,100]"/></hierarchy>'
    )
    assert nodes[0]["content_desc"] == "Login"
    assert nodes[0]["enabled"] is False
    assert nodes[0]["package"] == "com.example.app"


@pytest.mark.asyncio
async def test_selector_accepts_production_xml_string_bounds():
    from artemis.mcp.direct_selectors import DirectSelectors, Selector
    actuator = MockActuator(width=720, height=1280)
    screen = await actuator.driver.get_screen_data()
    screen.ui_elements = [{"text": "Login", "resource-id": "app:id/login",
                          "bounds": "[100,200][300,400]", "enabled": "true"}]
    actuator.driver.get_screen_data = AsyncMock(return_value=screen)
    result = await DirectSelectors(actuator).click(Selector(resource_id="app:id/login"))
    assert result.ok
    assert abs(actuator.action_history[-1]["x"] - 200) <= 1
    assert abs(actuator.action_history[-1]["y"] - 300) <= 1
