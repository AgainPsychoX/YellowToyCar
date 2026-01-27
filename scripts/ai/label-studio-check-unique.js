// Script to check for unique frames in a video annotation task in Label Studio
// To be run in the browser console while viewing the video annotation task
{
	unique = [];
	canvas = $('.lsf-video canvas')[1];

	while (true) {
		const ctx = canvas.getContext("2d");
		const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
		
		const hashBuffer = await crypto.subtle.digest("SHA-256", imageData.data.buffer);
		
		const hash = [...new Uint8Array(hashBuffer)]
			.map(b => b.toString(16).padStart(2, "0"))
			.join("");
		
		const [id, maxId] = $('.lsf-frames-control')[0].innerText.split('\nof ').map(x => parseInt(x));
		if (unique.includes(hash)) {
			console.log(`${id} ${hash} DUPLICATE`);
		}
		else {
			console.log(`${id} ${hash} UNIQUE`);
			unique.push(hash);
		}

		if (id >= maxId) break;
		// if (id > 100)  break; // for testing

		$('button[aria-label="Step forward"]')[0].click()
		await new Promise(r => setTimeout(r, 100));
	}
}