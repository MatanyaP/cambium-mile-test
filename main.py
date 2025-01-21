# main.py
import json
import logging
import streamlit as st
from prompt_utils import load_article_contexts, get_instructions_template, display_article_and_keywords
from st_utils import get_logger

# Configure logger
logger = get_logger(__name__)

def check_password():
    """Returns `True` if the user had the correct password."""
    if "password_correct" not in st.session_state:
        st.text_input("Password", type="password", key="password", on_change=password_entered)
        return False
    return st.session_state["password_correct"]

def password_entered():
    """Checks whether a password entered by the user is correct."""
    if st.session_state["password"] == st.secrets["password"]:
        st.session_state["password_correct"] = True
        del st.session_state["password"]
    else:
        st.session_state["password_correct"] = False

def set_page_layout():
    """Set up the page layout with custom styles"""
    st.markdown("""
        <style>
            .chat-container {
                margin: 20px 0;
                padding: 20px;
                border-radius: 10px;
                background-color: #f8f9fa;
            }
            .webrtc-container {
                margin: 20px 0;
                text-align: center;
            }
        </style>
    """, unsafe_allow_html=True)

def get_js_code():
    """Return the JavaScript code as a string"""
    return """
        document.addEventListener('DOMContentLoaded', function() {
            console.log("Script loaded");

            // Add debug logging for audio context
            navigator.mediaDevices.getUserMedia({ audio: true })
                .then(() => console.log("Microphone permission granted"))
                .catch(err => console.error("Microphone error:", err));

            const startButton = document.getElementById('startButton');
            const stopButton = document.getElementById('stopButton');
            const statusDiv = document.getElementById('status');
            const errorDiv = document.getElementById('error');

            let peerConnection = null;
            let audioStream = null;
            let dataChannel = null;

            const INITIAL_INSTRUCTIONS = INSTRUCTIONS_PLACEHOLDER;
            const API_KEY = API_KEY_PLACEHOLDER;

            // Add event listeners
            startButton.addEventListener('click', init);
            stopButton.addEventListener('click', stopRecording);

            async function init() {
                startButton.disabled = true;
                try {
                    updateStatus('Initializing...');

                    // Connect directly to OpenAI's API
                    peerConnection = new RTCPeerConnection();
                    await setupAudio();
                    setupDataChannel();

                    const offer = await peerConnection.createOffer();
                    await peerConnection.setLocalDescription(offer);

                    const sdpResponse = await fetch(`https://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01`, {
                        method: "POST",
                        body: offer.sdp,
                        headers: {
                            Authorization: `Bearer ${API_KEY}`,
                            "Content-Type": "application/sdp",
                            "OpenAI-Beta": "realtime=v1"
                        },
                    });

                    if (!sdpResponse.ok) {
                        throw new Error(`OpenAI API error: ${sdpResponse.status}`);
                    }

                    const answer = {
                        type: "answer",
                        sdp: await sdpResponse.text(),
                    };
                    await peerConnection.setRemoteDescription(answer);

                    updateStatus('Connected');
                    stopButton.disabled = false;
                    hideError();

                } catch (error) {
                    startButton.disabled = false;
                    stopButton.disabled = true;
                    showError('Error: ' + error.message);
                    console.error('Initialization error:', error);
                    updateStatus('Failed to connect');
                }
            }

            async function setupAudio() {
                try {
                    // Create and configure audio element for playback
                    const audioEl = document.createElement("audio");
                    audioEl.autoplay = true;
                    document.body.appendChild(audioEl); // Add to DOM

                    // Set up audio stream from microphone
                    audioStream = await navigator.mediaDevices.getUserMedia({
                        audio: {
                            echoCancellation: true,
                            noiseSuppression: true,
                            sampleRate: 48000,
                            channelCount: 1
                        }
                    });

                    // Handle incoming audio
                    peerConnection.ontrack = (event) => {
                        console.log("Received audio track");
                        audioEl.srcObject = event.streams[0];
                    };

                    // Add microphone audio to peer connection
                    audioStream.getTracks().forEach(track => {
                        peerConnection.addTrack(track, audioStream);
                    });

                    console.log("Audio setup completed");
                } catch (error) {
                    console.error("Error setting up audio:", error);
                    throw error;
                }
            }

            function setupDataChannel() {
                dataChannel = peerConnection.createDataChannel("oai-events");
                dataChannel.onopen = onDataChannelOpen;
                dataChannel.onmessage = handleMessage;
                dataChannel.onerror = (error) => {
                    console.error("DataChannel error:", error);
                    showError("DataChannel error: " + error.message);
                };
                console.log("DataChannel setup completed");
            }

            function handleMessage(event) {
                try {
                    const message = JSON.parse(event.data);
                    console.log('Received message:', message);

                    switch (message.type) {
                        case "response.done":
                            handleTranscript(message);
                            break;
                        case "response.audio.delta":
                            handleAudioDelta(message);
                            break;
                        case "input_audio_buffer.speech_started":
                            console.log("Speech started");
                            createUserMessageContainer();
                            break;
                        case "input_audio_buffer.speech_ended":
                            console.log("Speech ended");
                            break;
                        case "conversation.item.input_audio_transcription.completed":
                            handleUserTranscript(message);
                            break;
                        case "error":
                            console.error("Error from API:", message.error);
                            showError(message.error.message);
                            break;
                        default:
                            console.log('Message type:', message.type);
                    }
                } catch (error) {
                    console.error('Error processing message:', error);
                    showError('Error processing message: ' + error.message);
                }
            }

            let currentUserMessage = null;

            function createUserMessageContainer() {
                const chatContainer = document.getElementById('chat-container');
                currentUserMessage = document.createElement('div');
                currentUserMessage.className = 'message user-message';

                const label = document.createElement('div');
                label.className = 'message-label';
                label.textContent = 'You';

                const content = document.createElement('div');
                content.className = 'message-content';

                currentUserMessage.appendChild(label);
                currentUserMessage.appendChild(content);
                chatContainer.appendChild(currentUserMessage);
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }

            function handleUserTranscript(message) {
                if (currentUserMessage && message.transcript) {
                    const content = currentUserMessage.querySelector('.message-content');
                    // If there's existing content, append a space before the new transcript
                    if (content.textContent) {
                        content.textContent = content.textContent + " " + message.transcript;
                    } else {
                        content.textContent = message.transcript;
                    }
                    const chatContainer = document.getElementById('chat-container');
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                }
            }

            function handleAudioDelta(message) {
                if (message.delta) {
                    console.log("Received audio data");
                }
            }

            function handleTranscript(message) {
                const chatContainer = document.getElementById('chat-container');

                if (message.response?.output?.[0]?.content?.[0]?.transcript) {
                    const transcript = message.response.output[0].content[0].transcript;

                    const botMessage = document.createElement('div');
                    botMessage.className = 'message bot-message';

                    const label = document.createElement('div');
                    label.className = 'message-label';
                    label.textContent = 'Assistant';

                    const content = document.createElement('div');
                    content.className = 'message-content';
                    content.textContent = transcript;

                    botMessage.appendChild(label);
                    botMessage.appendChild(content);
                    chatContainer.appendChild(botMessage);
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                }
            }

            function sendSessionUpdate() {
                const sessionUpdateEvent = {
                    "type": "session.update",
                    "session": {
                        "instructions": INITIAL_INSTRUCTIONS,
                        "modalities": ["text", "audio"],
                        "voice": "alloy",
                        "input_audio_format": "pcm16",
                        "output_audio_format": "pcm16",
                        "input_audio_transcription": {
                            "model": "whisper-1",
                        },
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 800,
                        }
                    }
                };
                sendMessage(sessionUpdateEvent);
            }

            function sendMessage(message) {
                if (dataChannel?.readyState === "open") {
                    dataChannel.send(JSON.stringify(message));
                    console.log('Sent message:', message);
                }
            }

            function onDataChannelOpen() {
                sendSessionUpdate();
                sendResponseCreate();
            }

            function sendResponseCreate() {
                sendMessage({ "type": "response.create" });
            }

            function stopRecording() {
                if (peerConnection) {
                    peerConnection.close();
                    peerConnection = null;
                }
                if (audioStream) {
                    audioStream.getTracks().forEach(track => track.stop());
                    audioStream = null;
                }
                if (dataChannel) {
                    dataChannel.close();
                    dataChannel = null;
                }
                startButton.disabled = false;
                stopButton.disabled = true;
                updateStatus('Ready to start');
            }

            function updateStatus(message) {
                statusDiv.textContent = message;
            }

            function showError(message) {
                errorDiv.style.display = 'block';
                errorDiv.textContent = message;
            }

            function hideError() {
                errorDiv.style.display = 'none';
            }
        });
    """

def get_webrtc_html(article_data):
    """Generate the HTML for WebRTC interface"""
    instructions = json.dumps(get_instructions_template(article_data['content']))
    api_key = json.dumps(st.secrets["OPENAI_API_KEY"])

    js_code = get_js_code()
    js_code = js_code.replace('INSTRUCTIONS_PLACEHOLDER', instructions)
    js_code = js_code.replace('API_KEY_PLACEHOLDER', api_key)

    html_template = '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Voice Chat</title>
        <style>
            .container {
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
            }
            .controls {
                text-align: center;
                margin: 20px 0;
            }
            .chat-container {
                margin: 20px 0;
                padding: 15px;
                border: 1px solid #ddd;
                border-radius: 5px;
                min-height: 300px;
                max-height: 500px;
                overflow-y: auto;
            }
            .message {
                margin: 10px 0;
                padding: 10px;
                border-radius: 8px;
                max-width: 80%;
            }
            .user-message {
                background-color: #e3f2fd;
                margin-left: auto;
                margin-right: 20px;
            }
            .bot-message {
                background-color: #f5f5f5;
                margin-left: 20px;
                margin-right: auto;
            }
            .message-label {
                font-size: 0.8em;
                color: #666;
                margin-bottom: 4px;
            }
            .status {
                text-align: center;
                margin: 10px 0;
                font-style: italic;
            }
            .error {
                color: red;
                display: none;
                margin: 10px 0;
            }
            button {
                padding: 10px 20px;
                margin: 0 10px;
                border-radius: 5px;
                border: none;
                background-color: #0066cc;
                color: white;
                cursor: pointer;
            }
            button:disabled {
                background-color: #cccccc;
                cursor: not-allowed;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="controls">
                <button id="startButton">Start Conversation</button>
                <button id="stopButton" disabled>End Conversation</button>
            </div>
            <div id="chat-container" class="chat-container"></div>
            <div id="status" class="status">Ready to start</div>
            <div id="error" class="error"></div>
        </div>
        <script>
            JAVASCRIPT_CODE_PLACEHOLDER
        </script>
    </body>
    </html>
    '''

    return html_template.replace('JAVASCRIPT_CODE_PLACEHOLDER', js_code)

def main():
    if not check_password():
        st.stop()

    set_page_layout()

    # Load article contexts
    article_contexts = load_article_contexts('resources/articles')

    # Add article selector dropdown
    article_options = ["Select Case Study..."] + list(article_contexts.keys())
    selected_article = st.selectbox(
        'Select Case Study',
        options=article_options,
        index=0,
        key='article_selector'
    )

    if selected_article != "Select Case Study...":
        selected_article_data = article_contexts.get(selected_article)
        if selected_article_data:
            # Display article content
            display_article_and_keywords(selected_article_data)

            # Create WebRTC container
            with st.container():
                st.components.v1.html(
                    get_webrtc_html(selected_article_data),
                    height=600
                )

if __name__ == '__main__':
    main()
