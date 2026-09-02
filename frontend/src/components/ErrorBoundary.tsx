import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[ErrorBoundary] Rendering error caught:", error);
    console.error("[ErrorBoundary] Component stack:", info.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            height: "100vh",
            background: "#030712",
            color: "#f8fafc",
            fontFamily: "'Inter', -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif",
            padding: "2rem",
            textAlign: "center",
            gap: "1.5rem",
          }}
        >
          <div
            style={{
              fontSize: "2.5rem",
              fontWeight: 700,
              color: "#f87171",
              letterSpacing: "-0.02em",
              fontFamily: "'JetBrains Mono', monospace",
            }}
          >
            //
          </div>
          <h1 style={{ fontSize: "1.4rem", fontWeight: 600, color: "#f8fafc", margin: 0 }}>
            界面渲染出现异常
          </h1>
          <p
            style={{
              fontSize: "0.875rem",
              color: "#64748b",
              maxWidth: "420px",
              lineHeight: 1.6,
              margin: 0,
            }}
          >
            这通常是一个临时问题，请尝试刷新页面。
            {this.state.error?.message && (
              <span style={{ display: "block", marginTop: "0.5rem", fontFamily: "'JetBrains Mono', monospace", fontSize: "0.75rem", color: "#475569", wordBreak: "break-all" }}>
                {this.state.error.message}
              </span>
            )}
          </p>
          <button
            onClick={this.handleRetry}
            style={{
              padding: "0.625rem 1.5rem",
              fontSize: "0.875rem",
              fontWeight: 600,
              color: "#f8fafc",
              background: "linear-gradient(135deg, #60a5fa 0%, #3b82f6 100%)",
              border: "1px solid rgba(96, 165, 250, 0.4)",
              borderRadius: "10px",
              cursor: "pointer",
              transition: "filter 0.15s",
              boxShadow: "0 4px 16px rgba(59, 130, 246, 0.28)",
            }}
            onMouseEnter={(e) => {
              (e.target as HTMLButtonElement).style.filter = "brightness(1.08)";
            }}
            onMouseLeave={(e) => {
              (e.target as HTMLButtonElement).style.filter = "brightness(1)";
            }}
          >
            重新加载
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
