// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { realtimeStore } from "@/stores/realtime";

import type { Canvas3DProps } from "./Canvas3D";
import { chooseMode, detectCapabilities, parseView, type Capabilities } from "./capabilities";
import { OfficeCanvas } from "./OfficeCanvas";
import { THEMES } from "./palette";
import { readTheme, THEME_STORAGE_KEY } from "./theme";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  realtimeStore.getState().reset();
});

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

describe("fetching the 2D board ahead of need", () => {
  // jsdom has no idle callbacks, so the page falls back to a delay; the 3D scene here is a stub
  const Scene = () => <div data-testid="scene" />;

  it("in 3D, fetches the board once the page is quiet — so a switch to 2D does not wait for it", () => {
    vi.useFakeTimers();
    try {
      const preload = vi.fn();
      render(<OfficeCanvas detect={() => DESKTOP} Scene={Scene} preload={preload} />);
      expect(preload).not.toHaveBeenCalled(); // not during start-up
      act(() => void vi.advanceTimersByTime(2000));
      expect(preload).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("asks once per visit, however often the view goes back and forth", () => {
    vi.useFakeTimers();
    try {
      const preload = vi.fn();
      const { rerender } = render(<OfficeCanvas view="3d" detect={() => DESKTOP} Scene={Scene} preload={preload} />);
      act(() => void vi.advanceTimersByTime(2000));
      rerender(<OfficeCanvas view="2d" detect={() => DESKTOP} Scene={Scene} preload={preload} />);
      rerender(<OfficeCanvas view="3d" detect={() => DESKTOP} Scene={Scene} preload={preload} />);
      act(() => void vi.advanceTimersByTime(2000));
      expect(preload).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("in 2D, does not: the board is already loading because it is on screen", () => {
    vi.useFakeTimers();
    try {
      const preload = vi.fn();
      render(<OfficeCanvas view="2d" detect={() => DESKTOP} Scene={Scene} preload={preload} />);
      act(() => void vi.advanceTimersByTime(5000));
      expect(preload).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("leaving before the page is quiet fetches nothing", () => {
    vi.useFakeTimers();
    try {
      const preload = vi.fn();
      const { unmount } = render(<OfficeCanvas detect={() => DESKTOP} Scene={Scene} preload={preload} />);
      unmount();
      act(() => void vi.advanceTimersByTime(5000));
      expect(preload).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});

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

  it("an office with nobody in it says so", () => {
    const { Scene } = sceneStub();
    const { rerender } = render(<OfficeCanvas detect={() => DESKTOP} Scene={Scene} />);
    expect(screen.getByRole("status").textContent).toContain("還沒有代理");
    expect(screen.getByRole("link", { name: "去雇用" }).getAttribute("href")).toBe("/agents");

    const fixture = JSON.parse(
      readFileSync(join(process.cwd(), "src/realtime/__fixtures__/contract.json"), "utf8"),
    ) as { snapshot_before: unknown };
    act(() => realtimeStore.getState().hydrate(fixture.snapshot_before));
    rerender(<OfficeCanvas detect={() => DESKTOP} Scene={Scene} />);
    expect(screen.queryByRole("status")).toBeNull();
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

  it("the look: chosen in the settings dialog, remembered in this browser (T-413)", () => {
    const { Scene, seen } = sceneStub();
    const { container } = render(<OfficeCanvas detect={() => DESKTOP} Scene={Scene} />);
    expect(seen.props!.theme).toBe("muji");
    // the office is not a settings screen: the choice lives behind a gear
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("button", { name: THEMES.industrial.label })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "辦公室設定" }));
    const picker = screen.getByRole("group", { name: "辦公室風格" });
    expect(picker.querySelectorAll("button")).toHaveLength(Object.keys(THEMES).length);

    fireEvent.click(screen.getByRole("button", { name: THEMES.industrial.label }));
    expect(seen.props!.theme).toBe("industrial");
    expect(container.querySelector("[data-office-theme]")?.getAttribute("data-office-theme")).toBe("industrial");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("industrial");
    expect(seen.mounts).toBe(1); // a new look, not a new canvas

    cleanup();
    const again = sceneStub();
    render(<OfficeCanvas detect={() => DESKTOP} Scene={again.Scene} />);
    expect(again.seen.props!.theme).toBe("industrial");
  });

  it("the board and back: the old canvas ending is not the new one failing", () => {
    // Switching to 2D destroys the 3D canvas, and the browser reports that as a lost context.
    // Coming back used to land on "3D 暫停" for a canvas that had only just been built.
    const { Scene, seen } = sceneStub();
    const props = { detect: () => DESKTOP, Scene };
    const { container, rerender } = render(<OfficeCanvas view="3d" {...props} />);
    act(() => seen.props!.onContextLost());
    expect(screen.getByRole("alert")).toBeTruthy();

    rerender(<OfficeCanvas view="2d" {...props} />);
    expect(mode(container)).toBe("2d");
    rerender(<OfficeCanvas view="3d" {...props} />);

    expect(mode(container)).toBe("3d");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByTestId("scene").getAttribute("data-frameloop")).toBe("always");
    expect(container.querySelector("[data-context-lost]")?.getAttribute("data-context-lost")).toBe("false");
  });

  it("a canvas that has already gone cannot report on the one that replaced it", () => {
    // React mounts, unmounts and mounts again in development, and the browser delivers the old
    // canvas's lost context afterwards — landing "3D 暫停" on a canvas that is drawing fine.
    const { Scene, seen } = sceneStub();
    const props = { detect: () => DESKTOP, Scene };
    const { container, rerender } = render(<OfficeCanvas view="3d" {...props} />);
    const reportFromTheOldCanvas = seen.props!.onContextLost;

    rerender(<OfficeCanvas view="2d" {...props} />);
    rerender(<OfficeCanvas view="3d" {...props} />);
    act(() => reportFromTheOldCanvas());

    expect(screen.queryByRole("alert")).toBeNull();
    expect(container.querySelector("[data-context-lost]")?.getAttribute("data-context-lost")).toBe("false");
    // and the canvas that is actually on screen is still heard
    act(() => seen.props!.onContextLost());
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("says which view it settled on, so the page can dress to match", () => {
    const { Scene } = sceneStub();
    const onMode = vi.fn();
    const { rerender } = render(<OfficeCanvas detect={() => DESKTOP} Scene={Scene} onMode={onMode} />);
    expect(onMode).toHaveBeenLastCalledWith("3d");

    rerender(<OfficeCanvas view="2d" detect={() => DESKTOP} Scene={Scene} onMode={onMode} />);
    expect(onMode).toHaveBeenLastCalledWith("2d");

    // and "auto" on a browser without WebGL 2 settles on the board without being asked
    cleanup();
    const again = sceneStub();
    const autoMode = vi.fn();
    render(<OfficeCanvas detect={() => ({ webgl2: false, narrow: false })} Scene={again.Scene} onMode={autoMode} />);
    expect(autoMode).toHaveBeenLastCalledWith("2d");
  });

  it("the settings dialog closes with Escape, the backdrop and its own button", () => {
    const { Scene } = sceneStub();
    render(<OfficeCanvas detect={() => DESKTOP} Scene={Scene} />);
    const gear = screen.getByRole("button", { name: "辦公室設定" });

    fireEvent.click(gear);
    expect(screen.getByRole("dialog")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(gear);
    fireEvent.click(screen.getByRole("button", { name: "關閉" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(gear.getAttribute("aria-expanded")).toBe("false");
  });

  it("an unknown or unreadable stored theme falls back to the default", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "neon");
    expect(readTheme()).toBe("muji");
    const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readTheme()).toBe("muji");
    get.mockRestore();
  });
});
