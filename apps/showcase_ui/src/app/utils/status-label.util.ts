export function formatTaskStatusLabel(status?: string | null): string {
  switch ((status || '').toLowerCase()) {
    case 'passed':
    case 'success':
      return '通过';
    case 'running':
      return '运行中';
    case 'paused':
      return '已暂停';
    case 'pending':
      return '排队中';
    case 'failed':
      return '失败';
    case 'cancelled':
    case 'canceled':
      return '已取消';
    case 'aborted':
      return '已中止';
    case 'idle':
      return '待执行';
    case 'completed':
      return '已完成';
    default:
      return '未知状态';
  }
}
