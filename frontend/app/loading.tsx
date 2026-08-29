export default function Loading() {
  return (
    <main className="route-state" aria-busy="true" aria-live="polite">
      <div className="route-state-card">
        <span className="route-state-mark" aria-hidden="true" />
        <p className="card-kicker">念念正在准备</p>
        <h1>正在打开家庭记忆</h1>
        <p>请稍候，已经保存的内容不会受到影响。</p>
      </div>
    </main>
  );
}
