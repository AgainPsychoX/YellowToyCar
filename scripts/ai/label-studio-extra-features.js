(() => {
	let xt = 0;
	function applyContrast(ctx, width, height, factor) {
		const imageData = ctx.getImageData(0, 0, width, height);
		const data = imageData.data;
		for (let i = 0; i < data.length; i += 4) {
			data[i]     = Math.min(255, Math.max(0, factor * (data[i]     - 128) + 128));
			data[i + 1] = Math.min(255, Math.max(0, factor * (data[i + 1] - 128) + 128));
			data[i + 2] = Math.min(255, Math.max(0, factor * (data[i + 2] - 128) + 128));
			// alpha stays as-is
		}

		ctx.putImageData(imageData, 0, 0);
	}
	document.addEventListener("keydown", (e) => {
		switch (e.key) {
			case "x":
			case "X":
				e.preventDefault();
				let fc = document.querySelectorAll(".lsf-video canvas");
				fc.length &&
					((fc = fc[0]),
					(fc.style.display = "block" === fc.style.display ? "none" : "block"),
					clearTimeout(xt),
					(xt = setTimeout(() => (fc.style.display = "block"), 3333)));
				break;
			case "f":
				e.preventDefault();
				let ic = $('.lsf-video canvas')[1];
				applyContrast(ic.getContext("2d"), ic.width, ic.height, 1.2);
				break;
			case "F":
				e.preventDefault();
				let dc = $('.lsf-video canvas')[1];
				applyContrast(dc.getContext("2d"), dc.width, dc.height, 0.8);
				break;
			case "p":
				e.preventDefault();
				$('.lsf-video__main')[0].style.height = '63dvh';
				break;
		}
	});
})();
