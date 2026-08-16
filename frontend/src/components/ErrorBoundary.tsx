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
            fontFamily: "'Inter', 'SF Mono', monospace",
            padding: "2rem",
            textAlign: "center",
            gap: "1.5rem",
          }}
        >
          <div
            style={{
              fontSize: "3rem",
              fontWeight: 700,
              color: "#f87171",
              letterSpacing: "-0.02em",
            }}
          >
            //
          </div>
          <h1
            style={{
              fontSize: "1.5rem",
              fontWeight: 600,
              color: "#f8fafc",
              margin: 0,
            }}
          >
            Something went wrong
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
            An unexpected rendering error occurred. This is likely a temporary
            issue — please try reloading the application.
          </p>
          <button
            onClick={this.handleRetry}
            style={{
              padding: "0.625rem 1.5rem",
              fontSize: "0.875rem",
              fontWeight: 500,
              color: "#f8fafc",
              background: "rgba(96, 165, 250, 0.12)",
              border: "1px solid rgba(148, 163, 184, 0.22)",
              borderRadius: "6px",
              cursor: "pointer",
              transition: "background 0.15s",
            }}
            onMouseEnter={(e) => {
              (e.target as HTMLButtonElement).style.background =
                "rgba(96, 165, 250, 0.22)";
            }}
            onMouseLeave={(e) => {
              (e.target as HTMLButtonElement).style.background =
                "rgba(96, 165, 250, 0.12)";
            }}
          >
            Retry
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
