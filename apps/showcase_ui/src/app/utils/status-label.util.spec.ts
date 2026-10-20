import { formatTaskStatusLabel } from './status-label.util';

describe('formatTaskStatusLabel', () => {
  it('formats supported execution states in Chinese', () => {
    expect(formatTaskStatusLabel('running')).toBe('运行中');
    expect(formatTaskStatusLabel('paused')).toBe('已暂停');
    expect(formatTaskStatusLabel('pending')).toBe('排队中');
    expect(formatTaskStatusLabel('completed')).toBe('已完成');
    expect(formatTaskStatusLabel('failed')).toBe('失败');
    expect(formatTaskStatusLabel('cancelled')).toBe('已取消');
  });

  it('uses a Chinese fallback for an unknown state', () => {
    expect(formatTaskStatusLabel('unexpected')).toBe('未知状态');
  });
});
