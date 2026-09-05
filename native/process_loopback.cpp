/**
 * process_loopback.cpp  -- WASAPI Application Loopback (C++ autónomo)
 *
 * Compilar con MinGW-w64 64-bit:
 *   g++ -shared -O2 -std=c++11 -Wno-attributes
 *       -o process_loopback.dll process_loopback.cpp
 *       -lole32 -static -static-libgcc -static-libstdc++
 *
 * Compilar con MSVC:
 *   cl /LD /O2 /EHsc process_loopback.cpp ole32.lib /OUT:process_loopback.dll
 */

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <objbase.h>
#include <atomic>
#include <cstring>

/* ── WAVEFORMATEX ────────────────────────────────────────────────────────── */
#ifndef WAVE_FORMAT_IEEE_FLOAT
#define WAVE_FORMAT_IEEE_FLOAT 3
#endif
#ifndef _WAVEFORMATEX_
#define _WAVEFORMATEX_
typedef struct _wfx {
    WORD  wFormatTag;
    WORD  nChannels;
    DWORD nSamplesPerSec;
    DWORD nAvgBytesPerSec;
    WORD  nBlockAlign;
    WORD  wBitsPerSample;
    WORD  cbSize;
} WAVEFORMATEX;
#endif

/* ── GUIDs ───────────────────────────────────────────────────────────────── */
static const GUID MY_IID_IUnknown =
    {0x00000000,0x0000,0x0000,{0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46}};
static const GUID MY_IID_IAudioClient =
    {0x1CB9AD4C,0xDBFA,0x4C32,{0xB1,0x78,0xC2,0xF5,0x68,0xA7,0x03,0xB2}};
static const GUID MY_IID_IAudioCaptureClient =
    {0xC8ADBD64,0xE71E,0x48A0,{0xA4,0xDE,0x18,0x5C,0x39,0x5C,0xD3,0x17}};
static const GUID MY_IID_ICompletionHandler =
    {0x41D949AB,0x9862,0x444A,{0x80,0xF6,0xC2,0x61,0x33,0x4D,0xA5,0xEB}};

/* ── Constantes WASAPI ───────────────────────────────────────────────────── */
#define AUDCLNT_SHAREMODE_SHARED           0
#define AUDCLNT_STREAMFLAGS_LOOPBACK       0x00020000u
#define AUDCLNT_STREAMFLAGS_EVENTCALLBACK  0x00040000u
#define AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM 0x80000000u
#define AUDCLNT_STREAMFLAGS_SRC_DEF_QUAL   0x08000000u
#define AUDCLNT_BUFFERFLAGS_SILENT         0x2u

/* AUDIOCLIENT_ACTIVATION_PARAMS — layout exacto del SDK de Windows */
typedef enum { AUDCLNT_ACT_DEFAULT=0, AUDCLNT_ACT_PROCESS=1 } MY_ACT_TYPE;
typedef enum { LOOP_INCLUDE=0, LOOP_EXCLUDE=1 }               MY_LOOP_MODE;

typedef struct {
    DWORD        TargetProcessId;     /* SDK usa TargetProcessId */
    MY_LOOP_MODE ProcessLoopbackMode;
} MY_LOOP_PARAMS;

typedef struct {
    MY_ACT_TYPE ActivationType;
    union {                           /* DUMMYUNIONNAME del SDK */
        MY_LOOP_PARAMS ProcessLoopbackParams;
    };
} MY_ACT_PARAMS;

/* ── Interfaces COM (C++ vtable pura) ───────────────────────────────────── */

MIDL_INTERFACE("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
IMyAudioClient : public IUnknown {
public:
    virtual HRESULT STDMETHODCALLTYPE Initialize(int,DWORD,LONGLONG,LONGLONG,WAVEFORMATEX*,LPCGUID)=0;
    virtual HRESULT STDMETHODCALLTYPE GetBufferSize(UINT32*)=0;
    virtual HRESULT STDMETHODCALLTYPE GetStreamLatency(LONGLONG*)=0;
    virtual HRESULT STDMETHODCALLTYPE GetCurrentPadding(UINT32*)=0;
    virtual HRESULT STDMETHODCALLTYPE IsFormatSupported(int,WAVEFORMATEX*,WAVEFORMATEX**)=0;
    virtual HRESULT STDMETHODCALLTYPE GetMixFormat(WAVEFORMATEX**)=0;
    virtual HRESULT STDMETHODCALLTYPE GetDevicePeriod(LONGLONG*,LONGLONG*)=0;
    virtual HRESULT STDMETHODCALLTYPE Start()=0;
    virtual HRESULT STDMETHODCALLTYPE Stop()=0;
    virtual HRESULT STDMETHODCALLTYPE Reset()=0;
    virtual HRESULT STDMETHODCALLTYPE SetEventHandle(HANDLE)=0;
    virtual HRESULT STDMETHODCALLTYPE GetService(REFIID,void**)=0;
};

MIDL_INTERFACE("C8ADBD64-E71E-48A0-A4DE-185C395CD317")
IMyAudioCaptureClient : public IUnknown {
public:
    virtual HRESULT STDMETHODCALLTYPE GetBuffer(BYTE**,UINT32*,DWORD*,UINT64*,UINT64*)=0;
    virtual HRESULT STDMETHODCALLTYPE ReleaseBuffer(UINT32)=0;
    virtual HRESULT STDMETHODCALLTYPE GetNextPacketSize(UINT32*)=0;
};

MIDL_INTERFACE("72A22D78-CDE4-431D-B8CC-843A71199B6D")
IMyActivateAsyncOp : public IUnknown {
public:
    virtual HRESULT STDMETHODCALLTYPE GetActivateResult(HRESULT*,IUnknown**)=0;
};

MIDL_INTERFACE("41D949AB-9862-444A-80F6-C261334DA5EB")
IMyCompletionHandler : public IUnknown {
public:
    virtual HRESULT STDMETHODCALLTYPE ActivateCompleted(IMyActivateAsyncOp*)=0;
};

typedef HRESULT(WINAPI* PFN_Activate)(
    LPCWSTR, REFIID, PROPVARIANT*,
    IMyCompletionHandler*, IMyActivateAsyncOp**);

/* ── Estado global ───────────────────────────────────────────────────────── */
static std::atomic<bool>      g_running{false};
static HANDLE                 g_event  = NULL;
static IMyAudioClient*        g_client = nullptr;
static IMyAudioCaptureClient* g_cap    = nullptr;

typedef void (*AudioDataCallback)(const float*, UINT32, UINT32, UINT32);
static AudioDataCallback g_cb = nullptr;

/* ── Completion Handler ──────────────────────────────────────────────────── */
class CompletionHandler : public IMyCompletionHandler {
    std::atomic<ULONG> _ref{1};
public:
    HANDLE          done;
    HRESULT         result_hr;
    IMyAudioClient* audio_client;
    IUnknown*       m_pMarshaler;

    CompletionHandler()
        : done(CreateEventW(nullptr,TRUE,FALSE,nullptr))
        , result_hr(E_PENDING), audio_client(nullptr), m_pMarshaler(nullptr) {}
    ~CompletionHandler() { 
        if(done) CloseHandle(done); 
        if(m_pMarshaler) m_pMarshaler->Release();
    }

    ULONG STDMETHODCALLTYPE AddRef() override { return ++_ref; }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG r=--_ref; if(!r) delete this; return r;
    }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** ppv) override {
        static const GUID IID_IAgileObject = {0x94ea2b94, 0xe9cc, 0x49e0, {0xc0, 0xff, 0xee, 0x64, 0xca, 0x8f, 0x5b, 0x90}};
        if(!memcmp(&riid,&MY_IID_IUnknown,16)||
           !memcmp(&riid,&MY_IID_ICompletionHandler,16)||
           !memcmp(&riid,&IID_IAgileObject,16))
        { *ppv=static_cast<IMyCompletionHandler*>(this); AddRef(); return S_OK; }
        
        if (riid.Data1 == 0x00000003 && riid.Data2 == 0 && riid.Data3 == 0) { // IID_IMarshal
            if (!m_pMarshaler) CoCreateFreeThreadedMarshaler(static_cast<IMyCompletionHandler*>(this), &m_pMarshaler);
            if (m_pMarshaler) return m_pMarshaler->QueryInterface(riid, ppv);
        }
        *ppv=nullptr; return E_NOINTERFACE;
    }
    HRESULT STDMETHODCALLTYPE ActivateCompleted(IMyActivateAsyncOp* op) override {
        HRESULT hr=S_OK;
        IUnknown* unk=nullptr;
        op->GetActivateResult(&hr,&unk);
        result_hr=hr;
        if(SUCCEEDED(hr)&&unk){
            unk->QueryInterface(MY_IID_IAudioClient,
                                reinterpret_cast<void**>(&audio_client));
            unk->Release();
        }
        SetEvent(done);
        return S_OK;
    }
};

/* ── Contexto para el hilo STA de activación ────────────────────────────── */
struct ActivateCtx {
    DWORD   pid;
    int     include_tree;
    HRESULT hr;
    HANDLE  done;
};

static DWORD WINAPI ActivateSTA(LPVOID param)
{
    ActivateCtx* ctx = static_cast<ActivateCtx*>(param);

    /* Hilo STA dedicado — independiente del estado COM del hilo llamante */
    HRESULT hr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if(FAILED(hr)){ ctx->hr=hr; SetEvent(ctx->done); return 0; }

    /* Cargar ActivateAudioInterfaceAsync */
    HMODULE hMM = LoadLibraryW(L"mmdevapi.dll");
    if(!hMM){ ctx->hr=HRESULT_FROM_WIN32(GetLastError()); CoUninitialize(); SetEvent(ctx->done); return 0; }
    PFN_Activate pfnAct=(PFN_Activate)GetProcAddress(hMM,"ActivateAudioInterfaceAsync");
    if(!pfnAct){ FreeLibrary(hMM); ctx->hr=E_NOTIMPL; CoUninitialize(); SetEvent(ctx->done); return 0; }

    /* El "device path" para Process Loopback no es un GUID, es un string especial:
       #define VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK L"VAD\\Process_Loopback" */
    static const WCHAR VIRT_ID[] = L"VAD\\Process_Loopback";

    MY_ACT_PARAMS params={};
    params.ActivationType=AUDCLNT_ACT_PROCESS;
    params.ProcessLoopbackParams.TargetProcessId=(DWORD)ctx->pid;
    params.ProcessLoopbackParams.ProcessLoopbackMode=
        ctx->include_tree ? LOOP_INCLUDE : LOOP_EXCLUDE;

    PROPVARIANT pv={};
    pv.vt            =VT_BLOB;
    pv.blob.cbSize   =(ULONG)sizeof(MY_ACT_PARAMS);
    pv.blob.pBlobData=(BYTE*)&params;

    CompletionHandler* handler = new CompletionHandler();
    IMyActivateAsyncOp* async_op=nullptr;
    hr=pfnAct(VIRT_ID,MY_IID_IAudioClient,&pv,handler,&async_op);
    FreeLibrary(hMM);

    if(FAILED(hr)){ handler->Release(); ctx->hr=hr; CoUninitialize(); SetEvent(ctx->done); return 0; }

    /* Bombear el message loop STA mientras esperamos el completion callback.
       ActivateAudioInterfaceAsync en STA necesita que el hilo procese mensajes. */
    MSG msg;
    DWORD waitResult;
    while((waitResult=MsgWaitForMultipleObjects(1,&handler->done,FALSE,5000,QS_ALLEVENTS))
          ==WAIT_OBJECT_0+1)
    {
        while(PeekMessageW(&msg,nullptr,0,0,PM_REMOVE)){
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }

    hr=handler->result_hr;
    if(SUCCEEDED(hr)&&handler->audio_client){
        g_client=handler->audio_client;
    }
    if(async_op) async_op->Release();
    handler->Release();

    if(FAILED(hr)||!g_client){
        ctx->hr=hr?hr:E_FAIL;
        CoUninitialize();
        SetEvent(ctx->done);
        return 0;
    }

    /* Inicializar stream: float32 stereo 48kHz event-driven */
    WAVEFORMATEX wfx={};
    wfx.wFormatTag      =WAVE_FORMAT_IEEE_FLOAT;
    wfx.nChannels       =2;
    wfx.nSamplesPerSec  =48000;
    wfx.wBitsPerSample  =32;
    wfx.nBlockAlign     =(WORD)(wfx.nChannels*wfx.wBitsPerSample/8);
    wfx.nAvgBytesPerSec =wfx.nSamplesPerSec*wfx.nBlockAlign;
    wfx.cbSize          =0;

    hr=g_client->Initialize(
        AUDCLNT_SHAREMODE_SHARED,
        AUDCLNT_STREAMFLAGS_LOOPBACK|AUDCLNT_STREAMFLAGS_EVENTCALLBACK|
        AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM|AUDCLNT_STREAMFLAGS_SRC_DEF_QUAL,
        200000,0,&wfx,nullptr);
    if(FAILED(hr)){
        g_client->Release(); g_client=nullptr;
        ctx->hr=hr; CoUninitialize(); SetEvent(ctx->done); return 0;
    }

    g_event=CreateEventW(nullptr,FALSE,FALSE,nullptr);
    g_client->SetEventHandle(g_event);

    hr=g_client->GetService(MY_IID_IAudioCaptureClient,
                            reinterpret_cast<void**>(&g_cap));
    if(FAILED(hr)){
        CloseHandle(g_event); g_event=NULL;
        g_client->Release(); g_client=nullptr;
        ctx->hr=hr; CoUninitialize(); SetEvent(ctx->done); return 0;
    }

    g_client->Start();
    g_running=true;
    ctx->hr=S_OK;
    SetEvent(ctx->done);

    /* ── Hilo de captura (loop hasta que g_running sea false) ── */
    typedef HANDLE(WINAPI*PFN_Set)(LPCWSTR,DWORD*);
    typedef BOOL  (WINAPI*PFN_Rev)(HANDLE);
    HMODULE hAvrt=LoadLibraryW(L"avrt.dll");
    HANDLE task=NULL;
    if(hAvrt){
        PFN_Set pSet=(PFN_Set)GetProcAddress(hAvrt,"AvSetMmThreadCharacteristicsW");
        DWORD idx=0;
        if(pSet) task=pSet(L"Pro Audio",&idx);
    }
    static float zeroes[9600]={};
    while(g_running){
        if(WaitForSingleObject(g_event,200)!=WAIT_OBJECT_0) continue;
        UINT32 pkt=0;
        while(g_cap&&SUCCEEDED(g_cap->GetNextPacketSize(&pkt))&&pkt>0){
            BYTE* data=nullptr; UINT32 frms=0; DWORD flags=0;
            if(FAILED(g_cap->GetBuffer(&data,&frms,&flags,nullptr,nullptr))) break;
            if(g_cb&&frms>0){
                if(flags&AUDCLNT_BUFFERFLAGS_SILENT)
                    g_cb(zeroes,frms<4800?frms:4800,2,48000);
                else
                    g_cb(reinterpret_cast<const float*>(data),frms,2,48000);
            }
            g_cap->ReleaseBuffer(frms);
        }
    }
    if(task&&hAvrt){
        PFN_Rev pRev=(PFN_Rev)GetProcAddress(hAvrt,"AvRevertMmThreadCharacteristics");
        if(pRev) pRev(task);
        FreeLibrary(hAvrt);
    }

    /* Limpieza del stream */
    if(g_client) g_client->Stop();
    if(g_cap)   { g_cap->Release();    g_cap=nullptr; }
    if(g_client){ g_client->Release(); g_client=nullptr; }
    if(g_event) { CloseHandle(g_event); g_event=NULL; }

    CoUninitialize();
    return 0;
}

/* ── Funciones exportadas ────────────────────────────────────────────────── */
extern "C" {

__declspec(dllexport)
void set_audio_callback(AudioDataCallback cb){ g_cb=cb; }

__declspec(dllexport)
int start_capture(UINT32 pid, int include_tree)
{
    ActivateCtx ctx;
    ctx.pid          = pid;
    ctx.include_tree = include_tree;
    ctx.hr           = E_FAIL;
    ctx.done         = CreateEventW(nullptr,TRUE,FALSE,nullptr);

    /* Crear hilo STA dedicado — evita que el estado COM de Python interfiera */
    HANDLE hThr = CreateThread(nullptr, 0, ActivateSTA, &ctx, 0, nullptr);
    if(!hThr){ CloseHandle(ctx.done); return E_FAIL; }

    /* Esperar a que la activación complete o falle (máx 8s) */
    WaitForSingleObject(ctx.done, 8000);
    CloseHandle(ctx.done);
    CloseHandle(hThr);

    return (int)ctx.hr;
}

__declspec(dllexport)
void stop_capture()
{
    g_running=false;
    Sleep(400);  /* esperar que el hilo de captura salga */
}

__declspec(dllexport)
int is_running(){ return g_running.load()?1:0; }

} /* extern "C" */
