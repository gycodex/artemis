import {
  formatDirectStatusLabel,
  localizeDirectDisplayText,
  localizeDirectReportText
} from './agent-stream.component';

describe('Direct history presentation helpers', () => {
  it('formats Direct session statuses in Chinese', () => {
    expect(formatDirectStatusLabel('passed')).toBe('通过');
    expect(formatDirectStatusLabel('failed')).toBe('失败');
    expect(formatDirectStatusLabel('running')).toBe('运行中');
    expect(formatDirectStatusLabel(undefined)).toBe('已完成');
  });

  it('localizes common diagnostics terminology', () => {
    expect(localizeDirectDisplayText('Native Heap 43MB, TOTAL PSS 114MB, no Logcat errors'))
      .toBe('原生堆内存 43MB, 总内存占用（PSS） 114MB, no 系统日志 errors');
  });

  it('localizes the generated report structure and common actions', () => {
    const report = [
      '- Status: **passed**',
      '- Checkpoints: 4/4 passed',
      '## Summary',
      '## Actions',
      "1. `click` — OK: Tapped at [10, 20] (normalized).",
      "2. `press_key` — OK: Pressed key 'back'.",
      '## Evidence Screenshots'
    ].join('\n');

    const localized = localizeDirectReportText(report);
    expect(localized).toContain('- 状态： **通过**');
    expect(localized).toContain('- 检查点： 4/4 通过');
    expect(localized).toContain('## 测试摘要');
    expect(localized).toContain('## 操作记录');
    expect(localized).toContain('`点击` — 成功： 点击归一化坐标 [10, 20]。');
    expect(localized).toContain('`按键` — 成功： 已按下“返回”键。');
    expect(localized).toContain('## 证据截图');
  });
});
