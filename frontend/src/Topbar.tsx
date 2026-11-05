export default function Topbar() {
  return (
    <header className="top">
      <span>{new Date().toLocaleDateString("zh-CN", {
        year: "numeric", month: "long", day: "numeric", weekday: "long",
      })}</span>
      <span>我的复习工作台</span>
    </header>
  );
}
