(function(global, factory) {
  if (typeof module === 'object' && typeof module.exports === 'object') {
    module.exports = factory();
  } else {
    global.SimplePlotAnimator = factory();
  }
})(typeof window !== 'undefined' ? window : this, function() {
  class SimplePlotAnimator {
    constructor(targetElement, options = {}) {
      this.targetElement = targetElement;
      this.svgElement = null;
      this.paths = [];
      this.currentPathIndex = 0;
      this.isPlaying = false;
      this.animationSpeed = options.speed || 3;
      this.currentPathTimer = null;
      this.onComplete = options.onComplete || null;
      this.onProgress = options.onProgress || null;
    }

    async loadFromURL(svgUrl) {
      try {
        const response = await fetch(svgUrl);
        if (!response.ok) {
          throw new Error(`Failed to load SVG: ${response.status}`);
        }

        const svgText = await response.text();
        this.targetElement.innerHTML = svgText;

        this.svgElement = this.targetElement.querySelector('svg');
        if (!this.svgElement) {
          throw new Error('No SVG found in response');
        }

        this.svgElement.style.width = '100%';
        this.svgElement.style.height = 'auto';

        this.preparePaths();
        this.updateProgress();

        return true;
      } catch (error) {
        console.error('SimplePlotAnimator - Error loading SVG:', error);
        this.targetElement.innerHTML = `<div class="plot-animator-error">Error loading SVG: ${error.message}</div>`;
        return false;
      }
    }

    preparePaths() {
      this.paths = Array.from(this.svgElement.querySelectorAll('path, polyline, polygon, line'));

      this.paths.forEach((path) => {
        const pathLength = this.getPathLength(path);
        path.style.opacity = '0';
        path.style.strokeDasharray = `${pathLength} ${pathLength}`;
        path.style.strokeDashoffset = pathLength;
      });
    }

    getPathLength(element) {
      if (element.getTotalLength) {
        return element.getTotalLength();
      }
      return 1000;
    }

    updateProgress() {
      if (this.onProgress) {
        this.onProgress({
          current: this.currentPathIndex,
          total: this.paths.length,
          percentage: Math.round((this.currentPathIndex / Math.max(this.paths.length, 1)) * 100)
        });
      }
    }

    play() {
      if (this.paths.length === 0) {
        return this;
      }

      this.isPlaying = true;
      this.animateCurrentPath();
      return this;
    }

    pause() {
      this.isPlaying = false;
      if (this.currentPathTimer) {
        clearTimeout(this.currentPathTimer);
      }
      return this;
    }

    reset() {
      this.pause();
      this.currentPathIndex = 0;
      this.preparePaths();
      this.updateProgress();
      return this;
    }

    setSpeed(speed) {
      this.animationSpeed = speed;
      return this;
    }

    animateCurrentPath() {
      if (!this.isPlaying || this.currentPathIndex >= this.paths.length) {
        this.isPlaying = false;
        if (this.onComplete) {
          this.onComplete();
        }
        return;
      }

      const currentPath = this.paths[this.currentPathIndex];
      const segmentDelay = Math.max(1, this.animationSpeed);
      currentPath.style.opacity = '1';
      currentPath.style.transition = `stroke-dashoffset ${segmentDelay}ms linear`;
      currentPath.style.strokeDashoffset = '0';

      this.updateProgress();

      this.currentPathTimer = setTimeout(() => {
        this.currentPathIndex += 1;
        this.animateCurrentPath();
      }, segmentDelay + 8);
    }

    static async create(targetElement, svgUrl, options = {}) {
      const animator = new SimplePlotAnimator(targetElement, options);
      const success = await animator.loadFromURL(svgUrl);
      return success ? animator : null;
    }
  }

  return SimplePlotAnimator;
});