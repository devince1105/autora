// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Canvas3DProps } from "./Canvas3D";
import { chooseMode, detectCapabilities, parseView, type Capabilities } from "./capabilities";
import { OfficeCanvas } from "./OfficeCanvas";

afterEach(cleanup);

const DESKTOP: Capabilities = { webgl2: true, narrow: false };

describe("3D or 2D", () => {
  it.each([
    [DESKTOP, "auto", "3d", "auto"],
    [DESKTOP, "2d", "2d", "selected"],
    [{ webgl2: true, narrow: true }, "auto", "2d", "narrow"],
    [{ webgl2: true, narrow: true }, "3d", "3d", "selected"],
    [{ webgl2: false, narrow: false }, "auto", "2d", "no-webgl2"],
    [{ webgl2: false, narrow: false }, "3d", "2d", "no-webgl2"],
  ] as const)("%o + %s -> %s (%s)", (caps, view, mode, reason) => {
    expect(chooseMode(caps, view)).toEqual({ mode, reason });
  });

  it("?view= accepts 3d and 2d only", () => {
    expect([parseView("3d"), parseView("2d"), parseView(null), parseView("vr")]).toEqual(["3d", "2d", "auto", "auto"]);
  });

  it("probes WebGL 2 and releases the probe context", () => {
    const loseContext = vi.fn();
    const win = (gl: unknown, narrow: boolean) =>
      ({
        document: { createElement: () => ({ getContext: () => gl }) },
        matchMedia: () => ({ matches: narrow }),
      }) as unknown as Window;
    const gl = { getExtension: () => ({ loseContext }) };
    expect(detectCapabilities(win(gl, false))).toEqual({ webgl2: true, narrow: false });
    expect(loseContext).toHaveBeenCalled();
    expect(detectCapabilities(win(null, true))).toEqual({ webgl2: false, narrow: true });
    const throwing = {
      document: { createElement: () => ({ getContext: () => { throw new Error("blocked"); } }) },
      matchMedia: () => ({ matches: false }),
    } as unknown as Window;
    expect(detectCapabilities(throwing).webgl2).toBe(false);
  });
});

/** Stands in for the WebGL scene: records its props and how often it was mounted. */
function sceneStub() {
  const seen = { mounts: 0, props: null as Canvas3DProps | null };
  function Scene(props: Canvas3DProps) {
    seen.props = props;
    useEffect(() => {
      seen.mounts += 1;
    }, []);
    return <div data-testid="scene" data-frameloop={props.frameloop} />;
  }
  return { Scene, seen };
}

const mode = (container: HTMLElement) => container.querySelector("[data-office-mode]")?.getAttribute("data-office-mode");

describe("OfficeCanvas", () => {
  it("without WebGL 2: the 2D board, and why", () => {
    const { Scene } = sceneStub();
    const { container } = render(<OfficeCanvas detect={() => ({ webgl2: false, narrow: false })} Scene={Scene} />);
    expect(mode(container)).toBe("2d");
    expect(screen.getByText(/不支援 WebGL 2/)).toBeTruthy();
    expect(screen.queryByTestId("scene")).toBeNull();
  });

  it("a narrow screen gets the board unless 3D is chosen", () => {
    const { Scene } = sceneStub();
    const narrow = () => ({ webgl2: true, narrow: true });
    const { container, rerender } = render(<OfficeCanvas detect={narrow} Scene={Scene} />);
    expect(mode(container)).toBe("2d");
    rerender(<OfficeCanvas view="3d" detect={narrow} Scene={Scene} />);
    expect(mode(container)).toBe("3d");
  });

  it("3D draws while visible and stops while the tab is hidden", () => {
    const { Scene } = sceneStub();
    render(<OfficeCanvas detect={() => DESKTOP} Scene={Scene} />);
    expect(screen.getByTestId("scene").getAttribute("data-frameloop")).toBe("always");

    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    act(() => void document.dispatchEvent(new Event("visibilitychange")));
    expect(screen.getByTestId("scene").getAttribute("data-frameloop")).toBe("never");
    hidden.mockReturnValue(false);
    act(() => void document.dispatchEvent(new Event("visibilitychange")));
    expect(screen.getByTestId("scene").getAttribute("data-frameloop")).toBe("always");
    hidden.mockRestore();
  });

  it("a lost WebGL context: overlay, no drawing, rebuilt only on request", () => {
    const { Scene, seen } = sceneStub();
    const onViewChange = vi.fn();
    const { container } = render(<OfficeCanvas detect={() => DESKTOP} Scene={Scene} onViewChange={onViewChange} />);
    expect(seen.mounts).toBe(1);

    act(() => seen.props!.onContextLost());
    expect(screen.getByRole("alert").textContent).toContain("3D 暫停");
    expect(screen.getByTestId("scene").getAttribute("data-frameloop")).toBe("never");
    expect(container.querySelector("[data-context-lost]")?.getAttribute("data-context-lost")).toBe("true");
    expect(seen.mounts).toBe(1); // no automatic retry

    fireEvent.click(screen.getByRole("button", { name: "重新建立 3D" }));
    expect(seen.mounts).toBe(2); // a fresh canvas, so a fresh context
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("scene").getAttribute("data-frameloop")).toBe("always");

    act(() => seen.props!.onContextLost());
    fireEvent.click(screen.getByRole("button", { name: "改用 2D 看板" }));
    expect(onViewChange).toHaveBeenCalledWith("2d");
  });

  it("the browser restoring the context by itself clears the overlay", () => {
    const { Scene, seen } = sceneStub();
    render(<OfficeCanvas detect={() => DESKTOP} Scene={Scene} />);
    act(() => seen.props!.onContextLost());
    act(() => seen.props!.onContextRestored());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(seen.mounts).toBe(1);
  });
});
