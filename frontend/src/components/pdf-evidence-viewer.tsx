import { useEffect, useRef, useState } from "react";
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

type BBoxCoordinateSpace = {
  width: number;
  height: number;
  origin: "top-left";
};

type PdfEvidenceViewerProps = {
  pdfUrl: string | null;
  pageIndex: number;
  bboxes: number[][];
  coordinateSpace: BBoxCoordinateSpace;
};

export function PdfEvidenceViewer({
  pdfUrl,
  pageIndex,
  bboxes,
  coordinateSpace,
}: PdfEvidenceViewerProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [renderSize, setRenderSize] = useState({ width: 0, height: 0 });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pdfUrl) {
      setPdf(null);
      return;
    }
    let task: PDFDocumentLoadingTask | null = null;
    let disposed = false;
    void import("pdfjs-dist")
      .then((pdfjs) => {
        if (disposed) return;
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        task = pdfjs.getDocument({ url: pdfUrl, verbosity: 0 });
        return task.promise;
      })
      .then((document) => {
        if (disposed || !document) return;
        setPdf(document);
        setError(null);
      })
      .catch(() => {
        if (!disposed) setError("PDF 加载失败");
      });
    return () => {
      disposed = true;
      void task?.destroy();
      setPdf(null);
    };
  }, [pdfUrl]);

  useEffect(() => {
    if (!pdf || !hostRef.current || !canvasRef.current) return;
    let renderTask: RenderTask | null = null;
    let disposed = false;
    const host = hostRef.current;
    const canvas = canvasRef.current;

    const render = async () => {
      try {
        const page = await pdf.getPage(pageIndex + 1);
        if (disposed) return;
        const baseViewport = page.getViewport({ scale: 1 });
        const scale = Math.max(0.1, host.clientWidth / baseViewport.width);
        const viewport = page.getViewport({ scale });
        const pixelRatio = window.devicePixelRatio || 1;
        canvas.width = Math.floor(viewport.width * pixelRatio);
        canvas.height = Math.floor(viewport.height * pixelRatio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        setRenderSize({ width: viewport.width, height: viewport.height });
        renderTask?.cancel();
        renderTask = page.render({
          canvas,
          viewport,
          transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
        });
        await renderTask.promise;
        setError(null);
      } catch (reason) {
        if (
          !disposed &&
          !(reason instanceof Error && reason.name === "RenderingCancelledException")
        ) {
          setError("PDF 页面渲染失败");
        }
      }
    };

    const observer = new ResizeObserver(() => void render());
    observer.observe(host);
    void render();
    return () => {
      disposed = true;
      observer.disconnect();
      renderTask?.cancel();
    };
  }, [pageIndex, pdf]);

  if (!pdfUrl) {
    return (
      <div className="grid aspect-[3/4] place-items-center text-xs text-muted-foreground">
        暂无原始 PDF
      </div>
    );
  }

  return (
    <div ref={hostRef} className="relative w-full overflow-hidden bg-white">
      <canvas ref={canvasRef} className="block" aria-label={`PDF 第 ${pageIndex + 1} 页`} />
      {renderSize.width > 0 &&
        bboxes.map((bbox, index) => {
          if (bbox.length !== 4) return null;
          const [x1, y1, x2, y2] = bbox;
          const left = (x1 / coordinateSpace.width) * renderSize.width;
          const top = (y1 / coordinateSpace.height) * renderSize.height;
          const width = ((x2 - x1) / coordinateSpace.width) * renderSize.width;
          const height = ((y2 - y1) / coordinateSpace.height) * renderSize.height;
          return (
            <span
              key={`${bbox.join("-")}-${index}`}
              className="pointer-events-none absolute border-2 border-amber-500 bg-amber-300/30 shadow-[0_0_0_1px_rgba(255,255,255,0.8)]"
              style={{ left, top, width, height }}
              aria-hidden="true"
            />
          );
        })}
      {error && (
        <div className="absolute inset-0 grid place-items-center bg-background/90 text-xs text-destructive">
          {error}
        </div>
      )}
    </div>
  );
}
