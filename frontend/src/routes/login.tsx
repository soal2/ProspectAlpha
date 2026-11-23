import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { login } from "@/lib/auth-session";

export const Route = createFileRoute("/login")({
  component: LoginPage,
  head: () => ({ meta: [{ title: "登录 · ProspectAlpha" }] }),
});

function LoginPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      await login(username, password);
      await navigate({ to: "/" });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "登录失败");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <form onSubmit={onSubmit} className="w-full max-w-sm rounded-2xl border border-border bg-card p-6">
        <h1 className="font-serif text-2xl font-semibold">登录</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          这是本部署的单一操作者入口。密码只提交给后端，不会写入前端构建产物。
        </p>
        <label className="mt-5 block text-sm font-medium" htmlFor="username">
          用户名
        </label>
        <input
          id="username"
          name="username"
          autoComplete="username"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3"
          required
        />
        <label className="mt-4 block text-sm font-medium" htmlFor="password">
          密码
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3"
          required
        />
        {error ? <p className="mt-3 text-sm text-destructive">{error}</p> : null}
        <button
          type="submit"
          disabled={pending}
          className="mt-5 h-10 w-full rounded-md bg-primary text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {pending ? "正在登录" : "进入工作台"}
        </button>
      </form>
    </div>
  );
}
