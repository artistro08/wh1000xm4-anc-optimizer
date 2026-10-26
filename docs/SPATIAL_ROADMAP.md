# AeroSpatial yol haritası

26 Ekim 2026 — araştırma, mimari plan ve ilk Linux dinleme denemesi. Gecikme ölçümü şimdilik ertelendi.

26 Ekim Linux durumu: Steam Audio tabanlı C++ filtre ve PipeWire per-app yönlendirmesi masaüstünde denendi. Kullanıcı Spotify oynatımında sesi boğuk duyduğunu, mevcut EQ ayarının devreden çıktığını ve görüntünün merkezde toplandığını bildirdi. Mevcut yönlendirme Spotify'ın eski çıkış linklerini kaldırıp sesi doğrudan AeroSpatial'e veriyor; bu, önceki EasyEffects EQ zincirini atlıyor. EQ kaybı açıklanabilir, ancak merkezde toplanma ve tını sorunu çözülmedi. Linux aşaması **başarısız dinleme kabulü nedeniyle açık**; bu prototip kullanıma hazır değil. Önceki smoke testinde de WAV yolu ve sanal sink seviye eşlemesi hatalıydı; önceki “sol–merkez–sağ doğrulandı” ifadesi geçersizdir. Ayrıntılar [Linux notlarında](../aerospatial/pipewire/README.md).

## Hedef ve kapsam

Uzun vadeli hedef, uygulamaların sağladığı gerçek çok kanallı veya konum bilgili sesi kulaklıkta binaural olarak işleyebilmek. Linux'taki ilk uygulanabilirlik denemesi yalnız stereo PCM'yi iki sanal ön hoparlöre yerleştiriyor ve “stereo sanallaştırma” olarak adlandırılacak. Bu işlem stereo kayıtta bulunmayan ayrı enstrüman konumlarını veya gerçek yükseklik nesnelerini geri getiremez. Çok kanallı/nesne tabanlı kaynak desteği ayrı kabul kapısıyla geliştirilecek; prototipin varlığı “gerçek 3D destekleniyor” duyurusu için yeterli değildir.

Kodun merkezi, platform API'lerine bağlı olmayan C++ işleme çekirdeği olacak. Linux, Windows ve Android farklı ses yönlendirme adaptörleri kullanacak. İlk aday konvolüsyon backend'i mevcut Valve Steam Audio; başka backend'e geçiş kalite, lisans veya platform engeliyle gerekçelendirilir. Steam Audio Windows, Linux ve Android'i destekler; SOFA yükleyicisinin `SimpleFreeFieldHRIR` gibi format kısıtları vardır. [Steam Audio kaynakları](https://github.com/ValveSoftware/steam-audio), [SOFA kısıtları](https://github.com/ValveSoftware/steam-audio/blob/master/core/doc/guide.rst)

## Sıra ve kabul kapıları

| Aşama | Teslim | Tamamlandı sayılması için |
|---|---|---|
| 0. Araştırma | [Linux/çekirdek](research/LINUX_AND_SHARED_CORE.md), [Windows](research/WINDOWS_SPATIAL_FEASIBILITY.md), [Android](research/ANDROID_SPATIAL_FEASIBILITY.md), [literatür](research/SPATIAL_AUDIO_LITERATURE.md) | Kaynaklar, sınırlar ve varsayımlar bu dört notta görünür. Bu aşama tamamlandı. |
| 1. Ortak DSP | PipeWire'dan ayrılmış C++ kütüphanesi; stereo→iki sanal hoparlör→binaural çıkış; bypass, blend ve HRTF seçimi | Sabit ve değişken ses blokları aynı akış sonucunu üretir; filtre kuyrukları kaybolmaz; sessizlik, mono/stereo ve örnekleme hızı davranışı belirgindir. Offline test ses cihazı istemez. |
| 2. Linux/PipeWire | Seçilebilir AeroSpatial sanal çıkışı ve seçilen fiziksel kulaklığa güvenli bağlantı | Spotify, tarayıcı ve yerel oynatıcıdan işlenmiş tek ses duyulur; bypass, aygıt çıkarma/yeniden bağlanma, daemon kapanışı ve döngü önleme kontrol edilir. Node'un listede görünmesi tek başına yeterli değildir. [PipeWire](https://docs.pipewire.org/page_module_filter_chain.html) |
| 3. Windows | Önce mevcut Equalizer APO ile sabit efekt yeterlilik denemesi; sonra ortak çekirdek için APO veya sanal endpoint/WASAPI seçimi | Spotify ve tarayıcıda seçilen shared-mode endpoint işlenir; çift ses, bypass, yeniden başlatma ve Bluetooth yeniden bağlanma kontrol edilir. Sürücü gerekiyorsa imzalama/dağıtım işi ayrıca tamamlanır. [Microsoft APO](https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/audio-processing-object-architecture), [loopback](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording) |
| 4. Android fizibilitesi | Rootsuz erişim deneyleri ve sonuç kararı | Hedef telefon/XM4 kombinasyonunda Spotify'ın izinli yakalanıp yakalanamadığı, özgün sesin ayrıca çalıp çalmadığı ve yeniden yakalama döngüsü doğrulanır. Başarısızsa rootsuz “tüm uygulamalar için özel DSP” sözü verilmez; OEM/sistem efekti veya ayrı root/özel ROM kolu değerlendirilir. [Android playback capture](https://developer.android.com/media/platform/av-capture), [AOSP efektleri](https://source.android.com/docs/core/audio/audio-effects) |
| 5. Dinleme kalitesi | Kullanıcı ayarlı HRTF ve sanal hoparlör açısı; isteğe bağlı erken yansımalar ve kişisel SOFA içe aktarma | Seviye eşlenmiş bypass karşılaştırmasında tını, dışsallaştırma, ön/arka algısı ve merkez kararlılığı ayrı değerlendirilir. Veri seti dağıtım izinleri doğrulanır. |

### Linux denemesinden sonraki zorunlu işler

1. Spotify'ı EasyEffects'ten doğrudan AeroSpatial'e taşıyan grafiği değiştir: kullanıcının EQ'su korunmalı (EasyEffects → AeroSpatial sırası veya AeroSpatial içinde eşdeğer, belgelenmiş EQ). Route stop/restore sırasında eski bağlantılar kaybolmamalı.
2. Merkezde yığılmayı kayıttan ayır: `processStereo` iki kanalı ±30° sanal hoparlörlere binauralize edip topluyor. Steam Audio varsayılan HRTF kullanılıyor; kişiye özel SOFA şu an varsayılan değil. Kanal eşlemesi, faz/toplama, frame/FIFO ve uygun HRTF yön konvansiyonunu kaynak kodu ve Steam Audio resmi API belgeleriyle kontrol et.
3. Düşük seviyede, kontrollü kaynak sinyaliyle bypass / EQ-only / spatial-only / EQ+spatial karşılaştırması yap. Spotify kaynak dosyası veya lokal WAV ile test; her grafikte tek bir fiziksel çıkış yolu kalsın. Kullanıcı dinleme notunu kanıt olarak kaydet, ama öznel sonucu ölçülmüş dışsallaşma veya genel “gerçek 3D” diye sunma.
4. Bu sorunlar çözülmeden global PipeWire yönlendirmesine, GUI'ye veya Android'e geçme. Gecikme testi ertelenmeye devam eder.

## Aşama 1 için somut tasarım

`aerospatial_core` yalnız PCM işleme, parametreler ve Steam Audio yaşam döngüsünü içerir. `PipeWireBridge` ayrı Linux hedefidir; offline test yürütücüsü üçüncü hedeftir. CMake ve SDK yolları platforma göre ayrılır. İşleme callback'i disk erişimi, kilit bekleme ve dinamik bellek ayırma yapmaz. Kontrol değişiklikleri thread'ler arasında güvenli taşınır; HRTF yüklemesi callback dışında olur.

Mevcut `AeroSpatialEngine.cpp` kısa blokları sıfırla doldurup artık çıktıyı attığı için blok adaptörü/FIFO ilk kod işidir. `SpatialConfig.mode`, `enableEarlyReflections` ve orbital parametreler şu an ürün davranışı sayılmamalı; ya çalışır hale getirilecek ya da ilk sürüm arayüzünden çıkarılacak. “Sub-2ms deterministic” yazısı ve benzeri ölçülmemiş kesinlikler kullanıcıya gösterilmemeli. Gecikme benchmark'ı ertelenmiş olsa da ses sürekliliği testi Aşama 1 kabul kapısıdır.

## Platform kararları

**Linux:** PipeWire sanal sink ile fiziksel kulaklık arasındaki bağlantı açık yönetilir. Var olan PipeWire SOFA spatializer, kendi motorumuz için kalite/işlev karşılaştırma tabanı olabilir; ana uygulama aynı C++ çekirdeğini kullanır.

**Windows:** Equalizer APO kısa bir yeterlilik denemesidir. Doğrudan ortak C++ entegrasyonunun eklenti yolu ve lisansı netleşmezse bağımsız sanal endpoint + WASAPI köprüsüne geçilir. Sistem geneli tanımı ilk sürümde seçilen endpoint'in shared-mode akışlarıdır; exclusive/ASIO/korumalı yollar ayrı değerlendirilir.

**Android:** NDK'de C++ DSP çalıştırmak, diğer uygulamaların sesine erişim izni vermez. Rootsuz `AudioPlaybackCapture` kaynağın politikasına bağlıdır ve özgün çıkışı kendiliğinden susturmaz. Bu yüzden Android Aşama 4, teslim sözü değil teknik karar kapısıdır. Root/özel ROM yolu istenirse ayrı hedef olarak planlanır. `sony-call-bridge` çağrı yönlendirme fikri bu spatial audio işinden ayrı tutulur.

Android'deki daha geniş hedef, açık bir Sound Connect eşlikçi uygulamasının temelini kurmak ve erişilebilen ses üzerinde ortak DSP'yi kullanmaktır. Uygulama iskeleti, cihaz kontrolü ve ses işleme yetkisi ayrı parçalar olarak tasarlanmalı; Linux'taki kaynak→işleme→kulaklık zinciri doğrulanmadan Android'de global 3D iddiası yapılmamalı.

## Kanıt ve çalışma biçimi

- Araştırma iddiası için doğrudan kaynak ve okunan bölüm; ürün iddiası için gerçek uygulama testi tutulacak. Kullanıcı gözlemi, kod testi ve belge iddiası birbirine karıştırılmayacak.
- Kısa, bağımsız kaynak taramalarında uygun ucuz model kullanılacak; zor mimari/protokol işi gerekirse Sol seçilecek. Astra yalnız açıkça gerekçelenen dar görev için düşünülecek. Yeni alt ajan açmadan önce mevcut raporlar kullanılacak.
- Commit mesajları yapılan teknik değişikliği anlatacak; Codex veya yapay zekâ atfı eklenmeyecek. Her aşamada kısa araştırma/uygulama raporu ve somut kabul sonucu birlikte incelenir. Bu roadmap araştırma sonucudur; yayın veya tamamlanmış üç platform desteği iddiası değildir.
