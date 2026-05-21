// ==UserScript==
// @name         PayPal Auto Filler (New SMS API)
// @namespace    http://tampermonkey.net/
// @version      49.1
// @description  完整照搬原版逻辑，替换为新短信接口，完善paylink对账
// @match        https://www.paypal.com/*
// @match        https://pay.openai.com/*
// @match        https://checkout.stripe.com/*
// @match        https://chatgpt.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_deleteValue
// @grant        GM_listValues
// @grant        GM_cookie
// @grant        GM_openInTab
// @grant        window.close
// @require      https://cdnjs.cloudflare.com/ajax/libs/crypto-js/4.1.1/crypto-js.min.js
// @connect hub.icoe.pp.ua
// @connect      meiguodizhi.com
// @connect      api2.suijidaquan.com
// @connect      a.62-us.com
// @run-at       document-idle
// ==/UserScript==

// ========== LinkHub 配置 ==========
var LINKHUB_CONFIG = {
    serverUrl: "https://hub.icoe.pp.ua",
    apiKey: "111",
    secretKey: "111"
};

// ========== 配置 ==========
var CONFIG = {
    maxCardRetries: 5,
    smsCheckInterval: 3000,
    smsMaxRetries: 20
};

// ========== 延迟(完全照搬原版) ==========
var DELAY = {
    stripe_PayPalClick: 12000,
    stripe_PayPalExpand: 1000,
    stripe_FillForm: 3000,
    stripe_Submit: 8000,
    stripe_ErrorCheck: 3000,
    paypal_FillEmail: 15000,
    paypal_Submit: 2000,
    checkout_CountryCheck: 5000,
    checkout_CountryChangeWait: 3000,
    checkout_Submit: 500,
    checkout_ErrorCheck: 4000,
    checkout_RetryDelay: 2000,
    code_FillDelay: 1000,
    review_ClickDelay: 2000,
    signup_Wait: 5000
};
// ==============================================

(function() {
    'use strict';
    var log = function(s) { console.log('[PP] ' + s); };

    function showStatus(msg, color) {
        var statusEl = document.getElementById('pp-status-overlay');
        if (!statusEl) {
            statusEl = document.createElement('div');
            statusEl.id = 'pp-status-overlay';
            statusEl.style.cssText = 'position:fixed;top:10px;right:10px;background:rgba(0,0,0,0.85);color:#0f0;padding:10px 15px;z-index:999999;font-size:13px;font-family:monospace;border-radius:5px;max-width:450px;word-break:break-all;pointer-events:none;';
            document.body.appendChild(statusEl);
        }
        var time = new Date().toLocaleTimeString();
        var msgDiv = document.createElement('div');
        msgDiv.style.cssText = 'margin:2px 0;color:' + (color || '#0f0') + ';';
        msgDiv.textContent = '[' + time + '] ' + msg;
        statusEl.appendChild(msgDiv);
        while (statusEl.children.length > 20) {
            statusEl.removeChild(statusEl.firstChild);
        }
        console.log('[PP] ' + msg);
    }

    var cardRetryCount = 0;

    function rand(min, max) {
        return Math.floor(Math.random() * (max - min + 1)) + min;
    }

    // ========== HMAC 签名请求 ==========
    function sendSecureRequest(endpoint, method, data, callback) {
        var timestamp = String(Math.floor(Date.now() / 1000));
        var nonce = String(Math.random() + Math.random());
        var bodyStr = data ? JSON.stringify(data) : "";
        var message = timestamp + nonce + bodyStr;
        var signature = CryptoJS.enc.Hex.stringify(CryptoJS.HmacSHA256(message, LINKHUB_CONFIG.secretKey));
        var requestConfig = {
            method: method,
            url: LINKHUB_CONFIG.serverUrl + endpoint,
            headers: {
                "Content-Type": "application/json",
                "X-LinkHub-Key": LINKHUB_CONFIG.apiKey,
                "X-LinkHub-Signature": signature,
                "X-LinkHub-Timestamp": timestamp,
                "X-LinkHub-Nonce": nonce
            },
            onload: function(res) {
                if (callback) callback(res.status, res.responseText);
            },
            onerror: function(err) {
                log('????: ' + endpoint + ' - ' + err);
                if (callback) callback(0, null);
            }
        };
        if (data) {
            requestConfig.data = bodyStr;
        }
        GM_xmlhttpRequest(requestConfig);
    }

    // ========== GM_cookie 清理(原版完整保留) ==========
    function deleteCookiesForDomain(domain) {
        return new Promise(function(resolve) {
            showStatus('🍪 删除 ' + domain + ' 的Cookies...', '#ff0');
            GM_cookie.list({ domain: domain }, function(cookies, error) {
                if (error) {
                    log('获取Cookie失败(' + domain + '): ' + error);
                    var altDomain = domain.replace(/^\./, '');
                    GM_cookie.list({ domain: altDomain }, function(cookies2, error2) {
                        if (error2) {
                            showStatus('⚠️ ' + domain + ' Cookie获取失败', '#ff0');
                            resolve();
                        } else {
                            deleteCookieList(cookies2, domain, resolve);
                        }
                    });
                    return;
                }
                deleteCookieList(cookies, domain, resolve);
            });
        });
    }

    function deleteCookieList(cookies, domain, resolve) {
        if (!cookies || cookies.length === 0) {
            log(domain + ' Cookie: 0个');
            showStatus('✅ ' + domain + ' Cookie已清(0个)', '#0f0');
            resolve();
            return;
        }
        var count = cookies.length;
        var deleted = 0;
        cookies.forEach(function(cookie) {
            var cookieDetails = {
                name: cookie.name,
                url: (cookie.secure ? 'https://' : 'http://') + cookie.domain + cookie.path,
                domain: cookie.domain,
                path: cookie.path
            };
            GM_cookie.delete(cookieDetails, function(result) {
                deleted++;
                if (deleted >= count) {
                    log(domain + ' Cookie删除: ' + count + '个');
                    showStatus('✅ ' + domain + ' Cookie已清(' + count + '个)', '#0f0');
                    resolve();
                }
            });
        });
        setTimeout(function() {
            log(domain + ' Cookie删除超时: ' + deleted + '/' + count);
            resolve();
        }, 3000);
    }

    function clearLocalStorage() {
        try { var c = localStorage.length; localStorage.clear(); log('localStorage: ' + c); } catch(e) {}
    }
    function clearSessionStorage() {
        try { var c = sessionStorage.length; sessionStorage.clear(); log('sessionStorage: ' + c); } catch(e) {}
    }
    async function clearIndexedDB() {
        try {
            if (!window.indexedDB || !window.indexedDB.databases) return;
            var dbs = await indexedDB.databases();
            for (var i = 0; i < dbs.length; i++) {
                await new Promise(function(r) {
                    var req = indexedDB.deleteDatabase(dbs[i].name);
                    req.onsuccess = r; req.onerror = r; req.onblocked = r;
                });
            }
            log('IndexedDB: ' + dbs.length);
        } catch(e) {}
    }

    async function cleanupAll() {
        showStatus('🧹 开始全面清理...', '#ff0');
        await deleteCookiesForDomain('chatgpt.com');
        await deleteCookiesForDomain('.chatgpt.com');
        showStatus('[重点] 清理PayPal Cookies...', '#f0f');
        await deleteCookiesForDomain('paypal.com');
        await deleteCookiesForDomain('.paypal.com');
        await deleteCookiesForDomain('www.paypal.com');
        await deleteCookiesForDomain('.www.paypal.com');
        await deleteCookiesForDomain('openai.com');
        await deleteCookiesForDomain('.openai.com');
        await deleteCookiesForDomain('pay.openai.com');
        await deleteCookiesForDomain('.pay.openai.com');
        await deleteCookiesForDomain('chatgpt.com');
        await deleteCookiesForDomain('.chatgpt.com');
        await deleteCookiesForDomain('stripe.com');
        await deleteCookiesForDomain('.stripe.com');
        await deleteCookiesForDomain('checkout.stripe.com');
        await deleteCookiesForDomain('.checkout.stripe.com');
        showStatus('📦 清理本地存储...', '#ff0');
        clearLocalStorage();
        clearSessionStorage();
        await clearIndexedDB();
        showStatus('🗑️ 清理脚本数据...', '#ff0');
        try {
            var keys = GM_listValues();
            for (var i = 0; i < keys.length; i++) GM_deleteValue(keys[i]);
            log('GM存储: ' + keys.length);
        } catch(e) {}
        showStatus('🎉 全部清理完成！', '#0f0');
        showStatus('🔒 跳转空白页...', '#ff0');
        setTimeout(function() {
            window.location.replace('about:blank');
        }, 1500);
    }

    // ========== 随机卡(原版) ==========
    function getNewCardInfo(cb) {
        GM_setValue('cardInfo', null);
        GM_xmlhttpRequest({
            method: 'POST',
            url: 'https://api2.suijidaquan.com/api/v2/random-credit-card',
            headers: {
                'accept': 'application/json, text/plain, */*',
                'content-type': 'application/json;charset=UTF-8',
                'origin': 'https://www.suijidaquan.com',
                'referer': 'https://www.suijidaquan.com/'
            },
            data: JSON.stringify({ count: "1", method: "random_credit_card" }),
            onload: function(r) {
                try {
                    var d = JSON.parse(r.responseText);
                    if (d.status === 'ok' && d.data && d.data.length > 0) {
                        var cd = d.data[0];
                        var info = {
                            cardNumber: cd.Credit_Card_Number,
                            cardExpiry: cd.Expires.replace('/', ' / '),
                            cardCvv: cd.CVV2,
                            cardType: cd.Credit_Card_Type,
                            timestamp: Date.now()
                        };
                        GM_setValue('cardInfo', info);
                        showStatus('新卡: ' + info.cardNumber, '#0f0');
                        cb(info);
                    } else { cb(getFallbackCard()); }
                } catch(e) { cb(getFallbackCard()); }
            },
            onerror: function() { cb(getFallbackCard()); }
        });
    }

    function getCardInfo(cb) {
        var cached = GM_getValue('cardInfo', null);
        if (cached && cached.timestamp && (Date.now() - cached.timestamp) < 5 * 60 * 1000) { cb(cached); return; }
        getNewCardInfo(cb);
    }

    function getFallbackCard() {
        return { cardNumber: '4539629041340603', cardExpiry: '03 / 2030', cardCvv: '620', cardType: 'Visa' };
    }

    function hasCardError() {
        var pageError = document.querySelector('[data-testid="page-level-error-message"]');
        if (pageError && pageError.textContent.includes('already been added to another PayPal account')) {
            showStatus('❌ 卡已添加到其他账户', '#f00'); return true;
        }
        var cardError = document.querySelector('#cardNumber-error');
        if (cardError && cardError.textContent.includes("isn't valid")) {
            showStatus('❌ 卡号无效', '#f00'); return true;
        }
        var cardInput = document.querySelector('#cardNumber');
        if (cardInput && cardInput.getAttribute('aria-invalid') === 'true') { return true; }
        return false;
    }

    function retryWithNewCard(submitFn) {
        cardRetryCount++;
        showStatus('🔄 换卡 #' + cardRetryCount + '/' + CONFIG.maxCardRetries, '#ff0');
        if (cardRetryCount > CONFIG.maxCardRetries) { cardRetryCount = 0; return; }
        getNewCardInfo(function(newCard) {
            fill('cardNumber', newCard.cardNumber);
            fill('cardExpiry', newCard.cardExpiry);
            fill('cardCvv', newCard.cardCvv);
            setTimeout(function() { if (submitFn) submitFn(); }, DELAY.checkout_RetryDelay);
        });
    }

    // ========== 新短信接口 ==========
    function acquireDynamicPhone(cb) {
        showStatus('🔄 正在向Link-Hub租借手机号...', '#ff0');
        sendSecureRequest('/api/sms/acquire-phone', 'POST', null, function(status, text) {
            if (status === 200) {
                try {
                    var d = JSON.parse(text);
                    if (d.phone_number) {
                        showStatus('✅ 独占锁定: ' + d.phone_number, '#0f0');
                        GM_setValue('active_locked_phone', d.phone_number);
                        cb(d.phone_number);
                        return;
                    }
                } catch(e) {}
            }
            showStatus('⚠️ 号池全忙，10秒后重试...', '#ff4444');
            setTimeout(function() { acquireDynamicPhone(cb); }, 10000);
        });
    }

    function fetchRemoteCode(phone, cb, retryCount) {
        retryCount = retryCount || 0;
        if (retryCount >= CONFIG.smsMaxRetries) { cb(null); return; }

        sendSecureRequest('/api/sms/fetch-code?phone_number=' + encodeURIComponent(phone), 'GET', null, function(status, text) {
            if (status === 200) {
                try {
                    var d = JSON.parse(text);
                    if (d.status === 'success' && d.code) {
                        showStatus('✅ 验证码: ' + d.code, '#0f0');
                        cb(d.code);
                        return;
                    }
                } catch(e) {}
            }
            setTimeout(function() { fetchRemoteCode(phone, cb, retryCount + 1); }, CONFIG.smsCheckInterval);
        });
    }

    // ========== 验证码弹窗检测(原版) ==========
    function isCodePopupVisible() {
        var popup = document.querySelector('[data-testid="sca-confirm-multi-field"]');
        if (popup && popup.offsetParent !== null) return true;
        var interstitial = document.querySelector('.xo-rc__open-interstitial');
        if (interstitial && interstitial.offsetParent !== null) return true;
        return false;
    }

    function fillCodeInputs(code) {
        showStatus('📝 填入验证码: ' + code, '#0f0');
        var digits = code.split('');
        for (var i = 0; i < 6; i++) {
            var input = document.getElementById('ci-ciBasic-' + i);
            if (input) {
                var ns = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                ns.call(input, digits[i]);
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }
        setTimeout(function() {
            var btn = document.querySelector('[data-testid="submit-button"]') || document.querySelector('.xo-rc__open-interstitial button');
            if (btn && !btn.disabled) { btn.click(); }
        }, DELAY.code_FillDelay);
    }

    function startCodeDetection() {
        var interval = setInterval(function() {
            if (isCodePopupVisible()) {
                clearInterval(interval);
                showStatus('🔔 验证码弹窗检测到，开始获取...', '#ff0');
                var phone = GM_getValue('active_locked_phone');
                if (phone) {
                    fetchRemoteCode(phone, function(code) {
                        if (code) fillCodeInputs(code);
                        else showStatus('❌ 超时未获取到验证码', '#f00');
                    });
                } else {
                    showStatus('❌ 无活跃手机号', '#f00');
                }
            }
        }, CONFIG.smsCheckInterval);
        setTimeout(function() { clearInterval(interval); }, 180000);
    }

    // ========== Review页面(原版) ==========
    function isReviewPage() {
        return !!document.getElementById('consentButton') || window.location.href.includes('/billingweb/review');
    }

    function handleReviewPage() {
        setTimeout(function() {
            var btn = document.getElementById('consentButton');
            if (btn) {
                if (btn.disabled) {
                    var check = setInterval(function() {
                        if (!btn.disabled) { clearInterval(check); btn.click(); }
                    }, 500);
                    setTimeout(function() { clearInterval(check); }, 30000);
                } else { btn.click(); }
            }
        }, DELAY.review_ClickDelay);
    }

    // ========== 隐藏人机验证(原版完整保留) ==========
    var st = document.createElement('style');
    st.textContent = '#captcha-standalone,.captcha-overlay,.captcha-container,.AddressAutocomplete-results{display:none!important}';
    document.head.appendChild(st);

    // ========== 随机生成(原版) ==========
    function randEmail() {
        var c = 'abcdefghijklmnopqrstuvwxyz0123456789', e = '';
        for (var i = 0; i < 16; i++) e += c[Math.floor(Math.random() * c.length)];
        return e + '@gmail.com';
    }

    function randPass() {
        var L = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ';
        var D = '0123456789', S = '!@#$%^', A = L + D + S;
        var p = L[Math.floor(Math.random()*26)] + L[26+Math.floor(Math.random()*26)] + D[Math.floor(Math.random()*10)] + S[Math.floor(Math.random()*6)];
        for (var i = 4; i < 14; i++) p += A[Math.floor(Math.random()*A.length)];
        return p.split('').sort(function(){return Math.random()-0.5}).join('');
    }

    // ========== 表单填充(原版完整保留) ==========
    function fill(id, val) {
        var el = document.getElementById(id);
        if (!el) return;
        var ns = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        ns.call(el, val);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
    }

    function fillSel(sel, val) {
        var el = document.querySelector(sel);
        if (!el) return;
        var ns = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
        ns.call(el, val);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.dispatchEvent(new Event('blur', { bubbles: true }));
    }

    function fillSelect(id, text) {
        var el = document.getElementById(id);
        if (!el) return;
        for (var i = 0; i < el.options.length; i++) {
            if (el.options[i].text.toLowerCase().includes(text.toLowerCase()) || el.options[i].value.toLowerCase().includes(text.toLowerCase())) {
                el.value = el.options[i].value;
                el.dispatchEvent(new Event('change', { bubbles: true }));
                return;
            }
        }
    }

    function getAddr(cb) {
        GM_xmlhttpRequest({
            method: 'POST',
            url: 'https://www.meiguodizhi.com/api/v1/dz',
            headers: { 'Content-Type': 'application/json' },
            data: JSON.stringify({ path: '/', method: 'address' }),
            onload: function(r) {
                try {
                    var d = JSON.parse(r.responseText);
                    var a = d.address || d;
                    cb({ street: a.Address || a.street || '123 Main St', city: a.City || a.city || 'New York', state: a.State_Full || a.State || a.state || 'New York', zip: (a.Zip_Code || a.zip || '10001').substring(0, 5) });
                } catch(e) { cb({ street:'123 Main St', city:'New York', state:'New York', zip:'10001' }); }
            },
            onerror: function() { cb({ street:'123 Main St', city:'New York', state:'New York', zip:'10001' }); }
        });
    }

    function clickBtn(retries, onSuccess) {
        retries = retries || 0;
        var btn = document.querySelector('button[data-testid="submit-button"]') ||
                  document.querySelector('button[data-testid="hosted-payment-submit-button"]') ||
                  document.querySelector('button[data-atomic-wait-intent="Submit_Email"]') ||
                  document.querySelector('button.SubmitButton--complete');
        if (!btn) {
            var all = document.querySelectorAll('button');
            for (var i = 0; i < all.length; i++) {
                var t = all[i].textContent.trim();
                if (t === '下一页' || t === 'Next' || t === 'Subscribe' || t === 'Pay' || t === 'Continue' || t === 'Agree' || t === 'Agree & Create Account' || t === 'Agree &amp; Create Account') {
                    btn = all[i]; break;
                }
            }
        }
        if (btn) {
            if (btn.disabled) {
                if (retries < 10) setTimeout(function() { clickBtn(retries + 1, onSuccess); }, 1000);
                return;
            }
            btn.click();
            if (onSuccess) setTimeout(onSuccess, 2000);
        } else {
            if (retries < 10) setTimeout(function() { clickBtn(retries + 1, onSuccess); }, 1000);
        }
    }

    function fillFullForm(addr, cardInfo, phone) {
        fill('email', randEmail());
        fill('phone', phone);
        fill('cardNumber', cardInfo.cardNumber);
        fill('cardExpiry', cardInfo.cardExpiry);
        fill('cardCvv', cardInfo.cardCvv);
        fill('password', randPass());
        fill('firstName', 'James');
        fill('lastName', 'Smith');
        fill('billingLine1', addr.street);
        fill('billingCity', addr.city);
        fill('billingPostalCode', addr.zip);
        fillSelect('billingState', addr.state);
    }

    function submitAndCheckError() {
        clickBtn(0, function() {
            setTimeout(function() {
                if (hasCardError()) {
                    retryWithNewCard(submitAndCheckError);
                } else {
                    cardRetryCount = 0;
                    startCodeDetection();
                }
            }, DELAY.checkout_ErrorCheck);
        });
    }

    // ========== 🔥 Paylink 通知网关函数 ==========
    function notifyGatewayPaylink(paylink, retryCount) {
        retryCount = retryCount || 0;
        showStatus('?? ????????: ' + paylink.substring(0, 80) + '... [?? ' + (retryCount + 1) + ']', '#0ff');
        sendSecureRequest('/api/worker/submit-verification', 'POST', { "pay_link": paylink }, function(status, text) {
            if (status === 200) {
                showStatus('? ???????', '#0f0');
                GM_setValue('cached_active_paylink', null);
                return;
            }

            showStatus('?? ??????: ' + status + ' - ' + (text || '').substring(0, 100), '#ff0');
            if (retryCount < 5) {
                setTimeout(function() {
                    notifyGatewayPaylink(paylink, retryCount + 1);
                }, 3000 * (retryCount + 1));
            }
        });
    }

    // ========== ???(????) ==========
    var host = window.location.host;
    var path = window.location.pathname;
    var fullCurrentUrl = window.location.href;

    showStatus('📄 ' + host + path, '#0ff');

    // ========== 🔥 chatgpt.com - 检测到跳转回来，通知网关 ==========
    if (host.includes('chatgpt.com')) {
        showStatus('🎯 检测到 chatgpt.com 跳转', '#0ff');

        // 从GM存储中读取之前缓存的paylink
        var savedPaylink = GM_getValue('cached_active_paylink', null);

        if (savedPaylink) {
            showStatus('?? ????paylink????????...', '#0ff');
            notifyGatewayPaylink(savedPaylink, 0);
        } else {
            showStatus('⚠️ 未找到缓存paylink，跳过网关通知', '#ff0');
        }

        // 执行清理
        cleanupAll();
        return;
    }

    // PayPal Review页面
    if (host.includes('paypal.com') && isReviewPage()) {
        handleReviewPage();
        return;
    }

    // ========== 🔥 OpenAI/Stripe页面 - 缓存paylink ==========
    if (host.includes('pay.openai.com') || host.includes('checkout.stripe.com')) {
        showStatus('🔗 OpenAI/Stripe 收银台 - 缓存paylink', '#0ff');

        // 缓存规范化 paylink，仅保留 /c/pay/cs_live_xxx 主体，避免 query/hash 干扰对账
        var normalizedPaylink = fullCurrentUrl.split('#')[0].split('?')[0];
        GM_setValue('cached_active_paylink', normalizedPaylink);
        log('已缓存paylink: ' + normalizedPaylink.substring(0, 100) + '...');

        setTimeout(function() {
            var paypalRadio = document.querySelector('#payment-method-accordion-item-title-paypal');
            if (paypalRadio) { paypalRadio.click(); }
            setTimeout(function() {
                var paypalExpandBtn = document.querySelector('[data-testid="paypal-accordion-item-button"]');
                if (paypalExpandBtn) { paypalExpandBtn.click(); }
            }, DELAY.stripe_PayPalExpand);
            setTimeout(function() {
                getAddr(function(addr) {
                    fillSel('#billingAddressLine1', addr.street);
                    fillSel('#billingLocality', addr.city);
                    fillSel('#billingPostalCode', addr.zip);
                    fillSelect('billingAdministrativeArea', addr.state);
                    var cb = document.getElementById('termsOfServiceConsentCheckbox');
                    if (cb && !cb.checked) { cb.click(); }
                    setTimeout(submitAndCheckError, DELAY.stripe_Submit);
                });
            }, DELAY.stripe_FillForm);
        }, DELAY.stripe_PayPalClick);
        return;
    }

    // PayPal登录页
    if (host.includes('paypal.com') && path === '/pay') {
        setTimeout(function() {
            fill('email', randEmail());
            setTimeout(clickBtn, DELAY.paypal_Submit);
        }, DELAY.paypal_FillEmail);
        return;
    }

    // PayPal结账页
    if (host.includes('paypal.com') && (path.includes('/checkoutweb/') || path.includes('/checkoutweb'))) {
        setTimeout(function() {
            if (hasCardError()) {
                retryWithNewCard(submitAndCheckError);
                return;
            }
            var country = document.getElementById('country');
            if (country && country.value !== 'US') {
                country.value = 'US';
                country.dispatchEvent(new Event('change', { bubbles: true }));
                setTimeout(doFill, DELAY.checkout_CountryChangeWait);
            } else { doFill(); }
        }, DELAY.signup_Wait);

        function doFill() {
            acquireDynamicPhone(function(allocatedPhone) {
                getCardInfo(function(cardInfo) {
                    getAddr(function(addr) {
                        fillFullForm(addr, cardInfo, allocatedPhone);
                        setTimeout(submitAndCheckError, DELAY.checkout_Submit);
                    });
                });
            });
        }
        return;
    }
})();