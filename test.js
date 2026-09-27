const WebSocket = require('ws');

// Connect directly to your UXP panel
const ws = new WebSocket('ws://localhost:39281');

ws.on('open', () => {
    console.log('Connected to Premiere UXP!');

    // Send the exact JSON your handleAction function expects
    const command = {
        requestId: "test-123",
        action: "sync_clips",
        params: { clipPaths: [] } 
    };

    ws.send(JSON.stringify(command));
    console.log('Command sent.');
});

ws.on('message', (data) => {
    console.log('Response from Premiere:', data.toString());
    process.exit(0);
});